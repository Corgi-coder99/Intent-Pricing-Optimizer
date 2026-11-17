"""Pipeline steps: landing -> bronze -> silver -> gold (+ housekeeping). Every step is idempotent."""
import hashlib
import json as _json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd

from src.pipeline.contracts import CONTRACTS, Contract, classify, clean
from src.pipeline.infra import (Layout, atomic_write_json, atomic_write_parquet, read_json, sha256_file)
from src.pipeline.policy import DEFAULT_POLICY, Policy

DATA_TYPES = [t for t in CONTRACTS if t != "tombstone"]
FILE_SUFFIXES = (".csv", ".jsonl", ".ndjson")


@dataclass
class Context:
    layout: Layout
    simulate_n: Optional[int] = None
    use_llm: bool = False
    policy: Policy = field(default_factory=lambda: DEFAULT_POLICY)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- LANDING (simulated source, optional)
def generate_simulated(ctx: Context) -> Dict:
    if not ctx.simulate_n:
        return {"skipped": "no simulation requested"}
    from src.data_generator import generate_ecommerce_dataset
    df = generate_ecommerce_dataset(n_samples=ctx.simulate_n, save=False)
    tmp = ctx.layout.landing / f"_sim_{ctx.simulate_n}.csv.tmp"
    df.to_csv(tmp, index=False)
    digest = sha256_file(tmp)[:12]
    final = ctx.layout.landing / f"simulated_{digest}.csv"     # content-addressed name -> never creates a duplicate
    if final.exists():
        tmp.unlink()
        return {"file": final.name, "new": False}
    tmp.replace(final)
    return {"file": final.name, "new": True}


# ---------------------------------------------------------------- BRONZE
def _read_header(path: Path) -> List[str]:
    if path.suffix.lower() == ".csv":
        return list(pd.read_csv(path, nrows=0).columns)
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return list(pd.read_json(path, lines=True, nrows=1).columns)
    raise ValueError(f"Unsupported format: {path.suffix}")


def iter_raw(path: Path, chunk_rows: int) -> Iterator[pd.DataFrame]:
    """Read in chunks so RAM does not grow with file size."""
    if path.suffix.lower() == ".csv":
        yield from pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], chunksize=chunk_rows)
    else:
        for chunk in pd.read_json(path, lines=True, dtype=False, chunksize=chunk_rows):
            yield chunk.astype(object)


def bronze_ingest(ctx: Context) -> Dict:
    """Register files in landing: hash, classify, copy immutably. Same-hash files are skipped; oversized files rejected;
    files still being written are deferred; at most `max_files_per_run` new files per run (the rest wait for the next run)."""
    L, pol = ctx.layout, ctx.policy
    manifest_path = L.state / "bronze_manifest.json"
    manifest: Dict[str, dict] = read_json(manifest_path, {})
    seen_path = L.state / "landing_seen.json"
    seen: Dict[str, list] = read_json(seen_path, {})         # name -> [size, mtime_ns, sha]: avoids re-hashing old files
    new = dup = unclassified = deferred = rejected = 0

    for f in sorted(L.landing.iterdir()):
        if not f.is_file() or f.suffix.lower() not in FILE_SUFFIXES or f.name.startswith("_"):
            continue
        st = f.stat()
        age = time.time() - st.st_mtime
        if age < pol.landing_settle_seconds:                  # might still be written: wait, then check again
            time.sleep(pol.landing_settle_seconds - age)
            st2 = f.stat()
            if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                deferred += 1
                continue
        cached = seen.get(f.name)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns and cached[2] in manifest:
            dup += 1
            continue
        if new >= pol.max_files_per_run:
            deferred += 1
            continue

        if st.st_size > pol.max_file_bytes:
            digest = hashlib.sha256(f"toolarge:{f.name}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()
            if digest not in manifest:
                manifest[digest] = {"file": f.name, "bronze_path": None, "record_type": None,
                                    "status": "rejected:too_large", "ingested_at": _now()}
                rejected += 1
            seen[f.name] = [st.st_size, st.st_mtime_ns, digest]
            continue

        digest = sha256_file(f)
        seen[f.name] = [st.st_size, st.st_mtime_ns, digest]
        if digest in manifest:
            dup += 1
            continue
        try:
            rtype = classify(_read_header(f))
            status = "registered" if rtype else "unclassified"
        except Exception as exc:                      # corrupt file: record it, do not crash the pipeline
            rtype, status = None, f"unreadable:{type(exc).__name__}"
        dest_dir = L.bronze / (rtype or "_unclassified")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{digest[:12]}_{f.name}"
        if not dest.exists():
            tmp = dest.with_name(dest.name + ".tmp")
            shutil.copy2(f, tmp)
            tmp.replace(dest)
        manifest[digest] = {"file": f.name, "bronze_path": str(dest.relative_to(L.root)), "record_type": rtype,
                            "status": status, "ingested_at": _now()}
        new += 1
        unclassified += rtype is None
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(seen_path, seen)
    return {"new_files": new, "duplicate_files_skipped": dup, "unclassified": unclassified,
            "deferred_files": deferred, "rejected_too_large": rejected}


# ---------------------------------------------------------------- SILVER
def _norm_part(value: str, kind: str) -> str:
    v = str(value).strip()
    try:
        if kind in ("float", "int"):
            return format(float(v), ".10g")
        if kind == "datetime":
            return pd.to_datetime(v, utc=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    return v


def key_strings(df: pd.DataFrame, contract: Contract) -> pd.Series:
    """Composite string key (numbers/dates normalised) for matching against tombstones."""
    out = None
    for c in contract.key:
        kind = contract.dtypes.get(c, "str")
        s = df[c]
        if kind in ("float", "int"):
            part = s.map(lambda v: format(float(v), ".10g"))
        elif kind == "datetime":
            part = pd.to_datetime(s, utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            part = s.astype(str).str.strip()
        out = part if out is None else out + "|" + part
    return out


def _row_hash(df: pd.DataFrame, cols: List[str]) -> pd.Series:
    norm = df[cols].astype(object).where(df[cols].notna(), "").astype(str)
    return pd.util.hash_pandas_object(norm, index=False)


def _changed_versions(existing: pd.DataFrame, new: pd.DataFrame, key: List[str]) -> pd.DataFrame:
    """Old rows with the same key but different content from the new version (about to be overwritten)."""
    cols = [c for c in existing.columns if c in new.columns and c not in key]
    e = existing[key].assign(_h=_row_hash(existing, cols).values)
    n = new[key].assign(_h=_row_hash(new, cols).values)
    changed = e.merge(n, on=key, suffixes=("_old", "_new"))
    changed = changed[changed["_h_old"] != changed["_h_new"]][key]
    return existing.merge(changed, on=key, how="inner")


def _archive_history(L: Layout, rtype: str, old: pd.DataFrame, key: List[str], batch: str, reason: str) -> int:
    """Store old versions / deleted rows in silver/history/<type>.parquet (deduplicated, so re-runs do not double up)."""
    hdir = L.silver / "history"
    hdir.mkdir(parents=True, exist_ok=True)
    path = hdir / f"{rtype}.parquet"
    cols = [c for c in old.columns if c not in key]
    old = old.assign(row_hash=_row_hash(old, cols).values, superseded_by_batch=batch, history_reason=reason,
                     superseded_at=pd.Timestamp.now(tz="UTC"))
    before = 0
    if path.exists():
        prev = pd.read_parquet(path)
        before = len(prev)
        old = pd.concat([prev, old], ignore_index=True)
    old = old.drop_duplicates(subset=[*key, "row_hash", "history_reason"], keep="first")
    atomic_write_parquet(old, path)
    return len(old) - before


def _apply_tombstones(current: Optional[pd.DataFrame], rows: pd.DataFrame, contract: Contract,
                      L: Layout, batch: str) -> Tuple[Optional[pd.DataFrame], int, int]:
    """Remove rows whose key matches from silver; removed rows are saved to history. Returns (new table, rows deleted, keys not found)."""
    wanted = set()
    for k in rows["record_key"].astype(str):
        parts = k.split("|")
        if len(parts) == len(contract.key):
            wanted.add("|".join(_norm_part(p, contract.dtypes.get(c, "str")) for p, c in zip(parts, contract.key)))
    if current is None or current.empty or not wanted:
        return current, 0, len(wanted)
    mask = key_strings(current, contract).isin(wanted)
    removed = current[mask]
    if len(removed):
        _archive_history(L, contract.name, removed, contract.key, f"tombstone:{batch}", "deleted")
    found = int(mask.sum())
    return current[~mask].reset_index(drop=True), found, max(0, len(wanted) - found)


def _state_path(L: Layout, rtype: str) -> Path:
    return L.state / f"silver_{rtype}_state.json"


def _load_state(L: Layout, rtype: str) -> Tuple[List[str], List[str]]:
    """Processed data batches and tombstone files for a record type. One file, so both lists are always consistent."""
    p = _state_path(L, rtype)
    if p.exists():
        d = read_json(p, {})
        return list(d.get("data", [])), list(d.get("tombstones", []))
    # legacy layout (two separate files) from earlier versions
    return (read_json(L.state / f"silver_{rtype}_processed.json", []),
            read_json(L.state / f"silver_{rtype}_tombstones_processed.json", []))


def _save_state(L: Layout, rtype: str, data: List[str], tombstones: List[str]) -> None:
    atomic_write_json(_state_path(L, rtype), {"data": data, "tombstones": tombstones})


def silver_build(ctx: Context, rtype: str) -> Dict:
    """Process in ingestion order: data batches (coerce types, validate, deduplicate, upsert, keep history) and
    tombstones (delete, keeping history). Idempotent: the processed list is written only after the silver table is written."""
    L, contract, pol = ctx.layout, CONTRACTS[rtype], ctx.policy
    manifest = read_json(L.state / "bronze_manifest.json", {})
    done_data, done_ts = _load_state(L, rtype)
    out_path = L.silver / f"{rtype}.parquet"

    events = []
    for h, m in manifest.items():                      # manifest insertion order = ingestion order
        if m.get("status") != "registered":
            continue
        if m["record_type"] == rtype and h not in done_data:
            events.append((h, m, "data"))
        elif m["record_type"] == "tombstone" and h not in done_ts:
            events.append((h, m, "tombstone"))
    current = pd.read_parquet(out_path) if out_path.exists() else None
    if not events:
        return {"batches": 0, "rows_total": 0 if current is None else len(current)}

    t = {"batches": 0, "tombstone_files": 0, "rows_in": 0, "rejected": 0, "duplicates_in_batch": 0,
         "duplicates_vs_existing": 0, "updated_rows": 0, "archived_versions": 0, "deleted_rows": 0,
         "tombstone_keys_not_found": 0, "quarantine_truncated": False}
    for digest, meta, kind in events:
        path = L.root / meta["bronze_path"]
        if kind == "tombstone":
            frames = [clean(c, CONTRACTS["tombstone"])[0] for c in iter_raw(path, pol.chunk_rows)]
            ts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["record_type", "record_key"])
            ts = ts[ts["record_type"].astype(str).str.lower() == rtype]
            current, found, missing = _apply_tombstones(current, ts, contract, L, digest[:12])
            t["deleted_rows"] += found
            t["tombstone_keys_not_found"] += missing
            t["tombstone_files"] += 1
            done_ts.append(digest)
            continue

        goods, bads, bad_kept = [], [], 0
        for chunk in iter_raw(path, pol.chunk_rows):
            good, bad, m = clean(chunk, contract)
            goods.append(good)
            t["rows_in"] += m["rows_in"]
            t["rejected"] += m["rejected"]
            t["duplicates_in_batch"] += m["duplicates_in_batch"]
            room = max(0, pol.max_quarantine_rows_per_batch - bad_kept)
            if len(bad):
                if room > 0:
                    bads.append(bad.head(room))
                    bad_kept += min(len(bad), room)
                if len(bad) > room:
                    t["quarantine_truncated"] = True
        if bads:
            qdir = L.quarantine / rtype
            qdir.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(pd.concat(bads, ignore_index=True).astype(str), qdir / f"{digest[:12]}.parquet")
        all_new = pd.concat(goods, ignore_index=True)
        batch_good = all_new.drop_duplicates(subset=contract.key, keep="last")
        t["duplicates_in_batch"] += len(all_new) - len(batch_good)

        if current is not None and len(current):
            old_versions = _changed_versions(current, batch_good, contract.key)
            if len(old_versions):
                t["archived_versions"] += _archive_history(L, rtype, old_versions, contract.key, digest[:12], "updated")
            t["updated_rows"] += len(old_versions)
            merged = pd.concat([current, batch_good], ignore_index=True)
        else:
            merged = batch_good
        before = len(merged)
        current = merged.drop_duplicates(subset=contract.key, keep="last").reset_index(drop=True)
        t["duplicates_vs_existing"] += before - len(current)
        t["batches"] += 1
        done_data.append(digest)

    if current is not None:
        atomic_write_parquet(current.sort_values(contract.key).reset_index(drop=True), out_path)
    _save_state(L, rtype, done_data, done_ts)           # written last: a crash midway means the next run reprocesses, and the result is still correct
    return {**t, "rows_total": 0 if current is None else len(current)}


# ---------------------------------------------------------------- HOUSEKEEPING (cleanup, size limits)
def housekeeping(ctx: Context) -> Dict:
    """Delete bronze files that are processed and past retention / over budget (the manifest keeps the hash, so re-dropping the same file is not counted twice),
    clean quarantine, trim old history, remove simulated files already ingested from landing."""
    import calendar
    L, pol = ctx.layout, ctx.policy
    manifest_path = L.state / "bronze_manifest.json"
    manifest: Dict[str, dict] = read_json(manifest_path, {})
    states = {t: _load_state(L, t) for t in DATA_TYPES}
    data_done = {t: set(states[t][0]) for t in DATA_TYPES}
    ts_done = {t: set(states[t][1]) for t in DATA_TYPES}

    def processed(h: str, m: dict) -> bool:
        rt = m["record_type"]
        if rt == "tombstone":
            return all(h in ts_done[t] for t in DATA_TYPES)
        if rt in data_done:
            return h in data_done[rt]
        return True                                    # unclassified / unreadable: nothing to process

    def size_of(m: dict) -> int:
        p = L.root / m["bronze_path"] if m.get("bronze_path") else None
        return p.stat().st_size if p and p.exists() else 0

    def age_ts(m: dict) -> float:
        return calendar.timegm(time.strptime(m["ingested_at"], "%Y-%m-%dT%H:%M:%SZ"))

    live = [(h, m) for h, m in manifest.items() if m.get("bronze_path") and (L.root / m["bronze_path"]).exists()]
    cutoff = time.time() - pol.bronze_retention_days * 86400
    freed = expired = 0

    def expire(m: dict, why: str) -> None:
        nonlocal freed, expired
        freed += size_of(m)
        (L.root / m["bronze_path"]).unlink(missing_ok=True)
        m["status"], m["expired_at"], m["expired_reason"] = "expired", _now(), why
        expired += 1

    for h, m in live:
        if processed(h, m) and age_ts(m) < cutoff:
            expire(m, "retention")
    live = [(h, m) for h, m in live if m["status"] != "expired"]
    total = sum(size_of(m) for _, m in live)
    for h, m in sorted(live, key=lambda x: age_ts(x[1])):          # oldest first
        if total <= pol.bronze_max_bytes:
            break
        if processed(h, m):
            total -= size_of(m)
            expire(m, "size_budget")
    atomic_write_json(manifest_path, manifest)

    q_deleted = 0
    qcut = time.time() - pol.quarantine_retention_days * 86400
    for p in L.quarantine.rglob("*.parquet"):
        if p.stat().st_mtime < qcut:
            p.unlink()
            q_deleted += 1

    h_dropped = 0
    hcut = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=pol.history_retention_days)
    hdir = L.silver / "history"
    for p in (hdir.glob("*.parquet") if hdir.exists() else []):
        hist = pd.read_parquet(p)
        keep = hist[hist["superseded_at"] >= hcut]
        if len(keep) != len(hist):
            h_dropped += len(hist) - len(keep)
            atomic_write_parquet(keep.reset_index(drop=True), p)

    landing_removed = 0
    for f in L.landing.glob("simulated_*.csv"):        # files generated by the pipeline itself; a copy already exists in bronze
        prefix = f.stem.split("_", 1)[1]
        if any(h.startswith(prefix) for h in manifest):
            f.unlink()
            landing_removed += 1
    return {"bronze_expired": expired, "bytes_freed": freed, "quarantine_deleted": q_deleted,
            "history_rows_dropped": h_dropped, "landing_removed": landing_removed}


# ---------------------------------------------------------------- GOLD
def gold_sessions(ctx: Context) -> Dict:
    """Enrich sessions with review signals. Each distinct review is analysed only ONCE (durable on-disk cache),
    so changing/adding data never re-calls the LLM for old reviews."""
    from src.ai_extractor import ReviewSignalExtractor
    ext = ReviewSignalExtractor(use_llm=ctx.use_llm)
    import inspect
    # extractor version = keywords + logic CODE (changing the logic invalidates the old cache automatically)
    logic = inspect.getsource(ReviewSignalExtractor._analyze_offline)
    mode = "llm:gpt-4o-mini" if ext.use_llm else "offline:" + hashlib.sha256(
        _json.dumps([ext.ISSUE_KEYWORDS, ext.POSITIVE_KEYWORDS, logic], sort_keys=True).encode()).hexdigest()[:10]
    cache_path = ctx.layout.state / "review_signal_cache.json"
    cache: Dict[str, dict] = read_json(cache_path, {})

    src = ctx.layout.silver / "session.parquet"
    if not src.exists():
        return {"skipped": "no session data yet"}
    df = pd.read_parquet(src)
    key = lambda t: hashlib.sha256(f"{mode}|{t}".encode()).hexdigest()
    new_calls = 0
    for text in df["recent_review_text"].unique():
        k = key(text)
        if k not in cache:
            cache[k] = ext.analyze_single_review(text)
            new_calls += 1
    if new_calls:
        atomic_write_json(cache_path, cache)

    extracted = pd.DataFrame([cache[key(t)] for t in df["recent_review_text"]], index=df.index)
    enriched = pd.concat([df, extracted], axis=1)
    atomic_write_parquet(enriched, ctx.layout.gold / "sessions_features.parquet")
    return {"rows": len(enriched), "reviews_analyzed_now": new_calls,
            "reviews_from_cache": int(df["recent_review_text"].nunique()) - new_calls, "mode": mode.split(":")[0]}


def gold_summaries(ctx: Context) -> Dict:
    from src import analytics
    src = ctx.layout.gold / "sessions_features.parquet"
    if not src.exists():
        return {"skipped": "no session features yet"}
    df = pd.read_parquet(src)
    atomic_write_parquet(analytics.region_summary(df), ctx.layout.gold / "region_summary.parquet")
    atomic_write_parquet(analytics.category_summary(df), ctx.layout.gold / "category_summary.parquet")
    return {"regions": df["region"].nunique(), "categories": df["category"].nunique()}


def robust_price_stats(df: pd.DataFrame, k: float = 5.0, min_obs: int = 5) -> pd.DataFrame:
    """Price statistics per (product, country, currency), robust against dirty/vandalised crowd data: points further than k times the
    median absolute deviation (MAD, floored at 2% of the median price) are excluded from min/median/max and counted separately."""
    keys = ["product_code", "country", "currency"]
    d = df.copy()
    d["_med"] = d.groupby(keys)["price"].transform("median")
    d["_dev"] = (d["price"] - d["_med"]).abs()
    d["_mad"] = d.groupby(keys)["_dev"].transform("median")
    d["_n"] = d.groupby(keys)["price"].transform("size")
    scale = (1.4826 * d["_mad"]).clip(lower=0.02 * d["_med"])
    d["is_outlier"] = (d["_n"] >= min_obs) & (d["_dev"] > k * scale)
    kept = d[~d["is_outlier"]]
    g = kept.groupby(keys).agg(n_obs=("price", "size"), n_stores=("store", "nunique"), min_price=("price", "min"),
                               median_price=("price", "median"), max_price=("price", "max"),
                               last_seen=("observed_at", "max")).reset_index()
    out = d.groupby(keys)["is_outlier"].sum().astype(int).rename("n_outliers").reset_index()
    g = g.merge(out, on=keys, how="left")
    g["confidence"] = ((g["n_stores"] >= 2) & (g["n_obs"] >= 3)).map({True: "ok", False: "low"})
    return g


def gold_competitor_prices(ctx: Context) -> Dict:
    path = ctx.layout.silver / "price_observation.parquet"
    if not path.exists():
        return {"skipped": "no price_observation data"}
    g = robust_price_stats(pd.read_parquet(path))
    atomic_write_parquet(g, ctx.layout.gold / "competitor_prices.parquet")
    return {"groups": len(g), "outliers_excluded": int(g["n_outliers"].sum()),
            "groups_with_2plus_stores": int((g["n_stores"] >= 2).sum())}


def _vat_table(ctx: Context) -> pd.DataFrame:
    """Reference VAT table. A user override at <data>/reference/vat_rates.csv takes priority, otherwise the packaged file."""
    override = ctx.layout.root / "reference" / "vat_rates.csv"
    path = override if override.exists() else Path(__file__).with_name("reference") / "vat_rates.csv"
    return pd.read_csv(path, comment="#").assign(country=lambda d: d["country"].str.upper())


def _benchmark_gross_margin(ctx: Context) -> Optional[float]:
    path = Path(__file__).with_name("reference") / "published_benchmarks.csv"
    if not path.exists():
        return None
    b = pd.read_csv(path, comment="#")
    v = b[(b["metric"] == "gross_margin_pct") & (b["entity"] == "Zalando SE")]["value"]
    return float(v.iloc[0]) if len(v) else None


def _sector_margins() -> Dict[str, float]:
    """Gross margin of supermarket/food retail (NACE G47.11) per country, source Eurostat (see src/connectors/eurostat_retail.py)."""
    path = Path(__file__).with_name("reference") / "eurostat_retail_margin.csv"
    if not path.exists():
        return {}
    t = pd.read_csv(path, comment="#")
    t = t[t["nace_r2"] == "G4711"]
    return dict(zip(t["country"], t["gross_margin_pct"]))


def market_margin(comp: pd.DataFrame, fx: Optional[pd.DataFrame], vat: pd.DataFrame,
                  costs: Optional[pd.DataFrame], home: str = "FR", max_fx_age_days: int = 10,
                  benchmark_gross_margin_pct: Optional[float] = None,
                  sector_gross_margin_pct: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """Market price net of VAT and converted to EUR per country, plus profit margin when branch cost is available.
    Market prices (Open Prices) are mostly food, so the reduced food VAT rate is used (column reduced_rate_food)."""
    rate = {"EUR": 1.0}
    if fx is not None and len(fx):
        latest = fx.sort_values("rate_date").groupby("currency").tail(1)
        newest = latest["rate_date"].max()
        fresh = latest[(newest - latest["rate_date"]) <= pd.Timedelta(days=max_fx_age_days)]   # drop stale rates (e.g. no longer published)
        rate.update(dict(zip(fresh["currency"], fresh["eur_per_unit"])))
    vat_rate = dict(zip(vat["country"], vat["reduced_rate_food"] / 100.0))

    d = comp.copy()
    d["vat_rate"] = d["country"].map(vat_rate)
    d["eur_per_unit"] = d["currency"].map(rate)
    d["status"] = "ok"
    d.loc[d["eur_per_unit"].isna(), "status"] = "fx_missing"
    d.loc[d["vat_rate"].isna() & (d["status"] == "ok"), "status"] = "vat_missing"
    ok = d["status"] == "ok"
    d["market_net_eur"] = (d["median_price"] / (1 + d["vat_rate"]) * d["eur_per_unit"]).where(ok)

    if costs is not None and len(costs):
        c = costs.copy()
        c["cost_rate"] = c["currency"].map(rate)
        c["unit_cost_eur"] = c["unit_cost"] * c["cost_rate"]
        if "own_price" in c.columns:
            c["own_vat"] = c["country"].map(vat_rate)
            c["own_net_eur"] = c["own_price"] / (1 + c["own_vat"]) * c["cost_rate"]
        else:
            c["own_net_eur"] = float("nan")
        d = d.merge(c[["product_code", "country", "unit_cost_eur", "own_net_eur"]], on=["product_code", "country"], how="left")
    else:
        d["unit_cost_eur"] = float("nan")
        d["own_net_eur"] = float("nan")

    d["margin_at_market_pct"] = ((d["market_net_eur"] - d["unit_cost_eur"]) / d["market_net_eur"] * 100).round(2)
    d["own_margin_pct"] = ((d["own_net_eur"] - d["unit_cost_eur"]) / d["own_net_eur"] * 100).round(2)
    d["own_vs_market_pct"] = ((d["own_net_eur"] / d["market_net_eur"] - 1) * 100).round(2)
    home_price = d[d["country"] == home].groupby("product_code")["market_net_eur"].median()
    d["vs_home_pct"] = ((d["market_net_eur"] / d["product_code"].map(home_price) - 1) * 100).round(2)
    d["home_country"] = home
    # SCENARIO (not real data): assume every branch buys at the same unit cost, and that cost gives a gross margin equal to
    # the published margin of another company (benchmark) when selling at the home-country market price. Shows how margin would change elsewhere.
    if benchmark_gross_margin_pct is not None:
        home_cost = d["product_code"].map(home_price) * (1 - benchmark_gross_margin_pct / 100.0)
        d["scenario_unit_cost_eur"] = home_cost.round(4)
        d["scenario_margin_pct"] = ((d["market_net_eur"] - home_cost) / d["market_net_eur"] * 100).round(2)
    else:
        d["scenario_unit_cost_eur"] = float("nan")
        d["scenario_margin_pct"] = float("nan")
    d["sector_gross_margin_pct"] = d["country"].map(sector_gross_margin_pct or {})
    cols = ["product_code", "country", "currency", "n_obs", "n_stores", "confidence", "status", "vat_rate", "eur_per_unit",
            "market_net_eur", "unit_cost_eur", "own_net_eur", "margin_at_market_pct", "own_margin_pct",
            "own_vs_market_pct", "vs_home_pct", "home_country", "scenario_unit_cost_eur", "scenario_margin_pct",
            "sector_gross_margin_pct"]
    return d[cols]


def gold_market_margin(ctx: Context, home: str = "FR") -> Dict:
    L = ctx.layout
    comp_path = L.gold / "competitor_prices.parquet"
    if not comp_path.exists():
        return {"skipped": "no competitor prices yet"}
    rd = lambda n: pd.read_parquet(L.silver / f"{n}.parquet") if (L.silver / f"{n}.parquet").exists() else None
    fx, costs = rd("fx_rate"), rd("branch_cost")
    sector = _sector_margins()
    anchor = sector.get(home)                                       # prefer the home country's food-sector margin (Eurostat)
    anchor_src = f"eurostat:{home} G4711" if anchor is not None else "zalando:2024"
    if anchor is None:
        anchor = _benchmark_gross_margin(ctx)
    out = market_margin(pd.read_parquet(comp_path), fx, _vat_table(ctx), costs, home,
                        benchmark_gross_margin_pct=anchor, sector_gross_margin_pct=sector)
    out["scenario_anchor"] = anchor_src if anchor is not None else None
    atomic_write_parquet(out, L.gold / "market_margin.parquet")
    multi = out.dropna(subset=["market_net_eur"]).groupby("product_code")["country"].nunique()
    return {"rows": len(out), "fx_missing": int((out["status"] == "fx_missing").sum()),
            "vat_missing": int((out["status"] == "vat_missing").sum()), "with_cost": int(out["unit_cost_eur"].notna().sum()),
            "products_in_2plus_countries": int((multi >= 2).sum()), "had_fx_data": fx is not None,
            "scenario_anchor": anchor_src if anchor is not None else None}
