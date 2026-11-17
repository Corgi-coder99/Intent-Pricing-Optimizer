"""Các bước của pipeline: landing -> bronze -> silver -> gold (+ housekeeping). Mọi bước idempotent."""
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


# ---------------------------------------------------------------- LANDING (nguồn mô phỏng, tuỳ chọn)
def generate_simulated(ctx: Context) -> Dict:
    if not ctx.simulate_n:
        return {"skipped": "no simulation requested"}
    from src.data_generator import generate_ecommerce_dataset
    df = generate_ecommerce_dataset(n_samples=ctx.simulate_n, save=False)
    tmp = ctx.layout.landing / f"_sim_{ctx.simulate_n}.csv.tmp"
    df.to_csv(tmp, index=False)
    digest = sha256_file(tmp)[:12]
    final = ctx.layout.landing / f"simulated_{digest}.csv"     # đặt tên theo nội dung -> không tạo bản trùng
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
    """Đọc theo khối để RAM không phình theo kích thước file."""
    if path.suffix.lower() == ".csv":
        yield from pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], chunksize=chunk_rows)
    else:
        for chunk in pd.read_json(path, lines=True, dtype=False, chunksize=chunk_rows):
            yield chunk.astype(object)


def bronze_ingest(ctx: Context) -> Dict:
    """Đăng ký file ở landing: hash, phân loại, sao chép bất biến. Trùng hash bị bỏ qua; file quá lớn bị từ chối;
    file đang ghi dở bị hoãn; mỗi lượt tối đa `max_files_per_run` file mới (phần còn lại để lượt sau)."""
    L, pol = ctx.layout, ctx.policy
    manifest_path = L.state / "bronze_manifest.json"
    manifest: Dict[str, dict] = read_json(manifest_path, {})
    seen_path = L.state / "landing_seen.json"
    seen: Dict[str, list] = read_json(seen_path, {})         # tên -> [size, mtime_ns, sha]: khỏi băm lại file cũ
    new = dup = unclassified = deferred = rejected = 0

    for f in sorted(L.landing.iterdir()):
        if not f.is_file() or f.suffix.lower() not in FILE_SUFFIXES or f.name.startswith("_"):
            continue
        st = f.stat()
        age = time.time() - st.st_mtime
        if age < pol.landing_settle_seconds:                  # có thể đang được ghi: đợi rồi kiểm tra lại
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
        except Exception as exc:                      # file hỏng: ghi nhận, không làm sập pipeline
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
            return format(float(v), "g")
        if kind == "datetime":
            return pd.to_datetime(v, utc=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    return v


def key_strings(df: pd.DataFrame, contract: Contract) -> pd.Series:
    """Khoá tổng hợp dạng chuỗi (chuẩn hoá số/ngày) để so khớp với tombstone."""
    out = None
    for c in contract.key:
        kind = contract.dtypes.get(c, "str")
        s = df[c]
        if kind in ("float", "int"):
            part = s.map(lambda v: format(float(v), "g"))
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
    """Các dòng cũ có cùng khoá nhưng nội dung khác bản mới (sắp bị ghi đè)."""
    cols = [c for c in existing.columns if c in new.columns and c not in key]
    e = existing[key].assign(_h=_row_hash(existing, cols).values)
    n = new[key].assign(_h=_row_hash(new, cols).values)
    changed = e.merge(n, on=key, suffixes=("_old", "_new"))
    changed = changed[changed["_h_old"] != changed["_h_new"]][key]
    return existing.merge(changed, on=key, how="inner")


def _archive_history(L: Layout, rtype: str, old: pd.DataFrame, key: List[str], batch: str, reason: str) -> int:
    """Lưu phiên bản cũ/dòng bị xoá vào silver/history/<loại>.parquet (khử trùng nên chạy lại không nhân đôi)."""
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
    """Xoá khỏi silver các dòng có khoá khớp; bản bị xoá được lưu vào history. Trả (bảng mới, số xoá, số khoá không tìm thấy)."""
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


def silver_build(ctx: Context, rtype: str) -> Dict:
    """Xử lý tuần tự theo thứ tự nạp: lô dữ liệu (ép kiểu, kiểm tra, khử trùng, upsert, lưu lịch sử) và
    tombstone (xoá có lưu lịch sử). Idempotent: danh sách đã xử lý chỉ ghi sau khi bảng silver đã ghi xong."""
    L, contract, pol = ctx.layout, CONTRACTS[rtype], ctx.policy
    manifest = read_json(L.state / "bronze_manifest.json", {})
    data_state = L.state / f"silver_{rtype}_processed.json"
    ts_state = L.state / f"silver_{rtype}_tombstones_processed.json"
    done_data: List[str] = read_json(data_state, [])
    done_ts: List[str] = read_json(ts_state, [])
    out_path = L.silver / f"{rtype}.parquet"

    events = []
    for h, m in manifest.items():                      # thứ tự chèn của manifest = thứ tự nạp
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
    atomic_write_json(data_state, done_data)           # ghi sau cùng: sập giữa chừng -> lượt sau xử lý lại, kết quả vẫn đúng
    atomic_write_json(ts_state, done_ts)
    return {**t, "rows_total": 0 if current is None else len(current)}


# ---------------------------------------------------------------- HOUSEKEEPING (dọn dẹp, giới hạn dung lượng)
def housekeeping(ctx: Context) -> Dict:
    """Xoá bronze đã xử lý xong và quá hạn/vượt ngân sách (manifest vẫn giữ hash nên file cũ nạp lại không bị tính 2 lần),
    dọn quarantine, cắt history cũ, xoá file mô phỏng đã nạp ở landing."""
    import calendar
    L, pol = ctx.layout, ctx.policy
    manifest_path = L.state / "bronze_manifest.json"
    manifest: Dict[str, dict] = read_json(manifest_path, {})
    data_done = {t: set(read_json(L.state / f"silver_{t}_processed.json", [])) for t in DATA_TYPES}
    ts_done = {t: set(read_json(L.state / f"silver_{t}_tombstones_processed.json", [])) for t in DATA_TYPES}

    def processed(h: str, m: dict) -> bool:
        rt = m["record_type"]
        if rt == "tombstone":
            return all(h in ts_done[t] for t in DATA_TYPES)
        if rt in data_done:
            return h in data_done[rt]
        return True                                    # unclassified / unreadable: không có gì để xử lý

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
    for h, m in sorted(live, key=lambda x: age_ts(x[1])):          # cũ nhất trước
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
    for f in L.landing.glob("simulated_*.csv"):        # file do chính pipeline sinh, đã có bản trong bronze
        prefix = f.stem.split("_", 1)[1]
        if any(h.startswith(prefix) for h in manifest):
            f.unlink()
            landing_removed += 1
    return {"bronze_expired": expired, "bytes_freed": freed, "quarantine_deleted": q_deleted,
            "history_rows_dropped": h_dropped, "landing_removed": landing_removed}


# ---------------------------------------------------------------- GOLD
def gold_sessions(ctx: Context) -> Dict:
    """Làm giàu phiên bằng tín hiệu review. Mỗi review khác nhau chỉ được phân tích MỘT lần (cache bền trên đĩa),
    nên đổi/thêm dữ liệu không phải gọi lại LLM cho review cũ."""
    from src.ai_extractor import ReviewSignalExtractor
    ext = ReviewSignalExtractor(use_llm=ctx.use_llm)
    mode = "llm:gpt-4o-mini" if ext.use_llm else "offline:" + hashlib.sha256(
        _json.dumps([ext.ISSUE_KEYWORDS, ext.POSITIVE_KEYWORDS], sort_keys=True).encode()).hexdigest()[:10]
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
    """Thống kê giá theo (sản phẩm, quốc gia, tiền tệ) chống dữ liệu cộng đồng bẩn/bị phá hoại: điểm lệch quá k lần
    độ lệch tuyệt đối trung vị (MAD, có sàn 2% giá trung vị) bị loại khỏi min/median/max và được đếm riêng."""
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
    """Bảng VAT tham chiếu. Ưu tiên file người dùng ghi đè ở <data>/reference/vat_rates.csv, nếu không dùng bản đóng gói."""
    override = ctx.layout.root / "reference" / "vat_rates.csv"
    path = override if override.exists() else Path(__file__).with_name("reference") / "vat_rates.csv"
    return pd.read_csv(path, comment="#").assign(country=lambda d: d["country"].str.upper())


def market_margin(comp: pd.DataFrame, fx: Optional[pd.DataFrame], vat: pd.DataFrame,
                  costs: Optional[pd.DataFrame], home: str = "FR") -> pd.DataFrame:
    """Giá thị trường đã trừ VAT và quy đổi về EUR theo quốc gia, kèm biên lợi nhuận nếu có giá vốn chi nhánh.
    Giá thị trường (Open Prices) chủ yếu là thực phẩm nên dùng mức VAT thực phẩm giảm (cột reduced_rate_food)."""
    rate = {"EUR": 1.0}
    if fx is not None and len(fx):
        latest = fx.sort_values("rate_date").groupby("currency").tail(1)
        rate.update(dict(zip(latest["currency"], latest["eur_per_unit"])))
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
    cols = ["product_code", "country", "currency", "n_obs", "n_stores", "confidence", "status", "vat_rate", "eur_per_unit",
            "market_net_eur", "unit_cost_eur", "own_net_eur", "margin_at_market_pct", "own_margin_pct",
            "own_vs_market_pct", "vs_home_pct", "home_country"]
    return d[cols]


def gold_market_margin(ctx: Context, home: str = "FR") -> Dict:
    L = ctx.layout
    comp_path = L.gold / "competitor_prices.parquet"
    if not comp_path.exists():
        return {"skipped": "no competitor prices yet"}
    rd = lambda n: pd.read_parquet(L.silver / f"{n}.parquet") if (L.silver / f"{n}.parquet").exists() else None
    fx, costs = rd("fx_rate"), rd("branch_cost")
    out = market_margin(pd.read_parquet(comp_path), fx, _vat_table(ctx), costs, home)
    atomic_write_parquet(out, L.gold / "market_margin.parquet")
    multi = out.dropna(subset=["market_net_eur"]).groupby("product_code")["country"].nunique()
    return {"rows": len(out), "fx_missing": int((out["status"] == "fx_missing").sum()),
            "vat_missing": int((out["status"] == "vat_missing").sum()), "with_cost": int(out["unit_cost_eur"].notna().sum()),
            "products_in_2plus_countries": int((multi >= 2).sum()), "had_fx_data": fx is not None}
