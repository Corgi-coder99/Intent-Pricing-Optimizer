"""Các bước của pipeline: landing -> bronze -> silver -> gold. Mọi bước idempotent (chạy lại không nhân đôi dữ liệu)."""
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.pipeline.contracts import CONTRACTS, classify, clean, norm_col
from src.pipeline.infra import (Layout, atomic_write_json, atomic_write_parquet, read_json, sha256_file)


@dataclass
class Context:
    layout: Layout
    simulate_n: Optional[int] = None
    use_llm: bool = False


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
    raise ValueError(f"Định dạng không hỗ trợ: {path.suffix}")


def read_raw(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    return pd.read_json(path, lines=True, dtype=False).astype(object)


def bronze_ingest(ctx: Context) -> Dict:
    """Đăng ký mọi file ở landing: tính hash, phân loại, sao chép bất biến. File trùng hash bị bỏ qua."""
    L = ctx.layout
    manifest_path = L.state / "bronze_manifest.json"
    manifest: Dict[str, dict] = read_json(manifest_path, {})
    new, dup, unclassified = 0, 0, 0

    for f in sorted(L.landing.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (".csv", ".jsonl", ".ndjson") or f.name.startswith("_"):
            continue
        digest = sha256_file(f)
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
                            "status": status, "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        new += 1
        unclassified += rtype is None
    atomic_write_json(manifest_path, manifest)
    return {"new_files": new, "duplicate_files_skipped": dup, "unclassified": unclassified}


# ---------------------------------------------------------------- SILVER
def silver_build(ctx: Context, rtype: str) -> Dict:
    """Ép kiểu + kiểm tra + khử trùng các file bronze của một loại, rồi upsert vào bảng silver."""
    L, contract = ctx.layout, CONTRACTS[rtype]
    manifest = read_json(L.state / "bronze_manifest.json", {})
    state_path = L.state / f"silver_{rtype}_processed.json"
    processed: List[str] = read_json(state_path, [])
    pending = [(h, m) for h, m in manifest.items()
               if m["record_type"] == rtype and h not in processed]
    out_path = L.silver / f"{rtype}.parquet"
    if not pending:
        return {"batches": 0, "rows_total": len(pd.read_parquet(out_path)) if out_path.exists() else 0}

    frames, totals = [], {"rows_in": 0, "rejected": 0, "duplicates_in_batch": 0}
    for digest, meta in pending:
        good, bad, m = clean(read_raw(L.root / meta["bronze_path"]), contract)
        frames.append(good)
        for k in totals:
            totals[k] += m[k]
        if len(bad):
            qdir = L.quarantine / rtype
            qdir.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(bad.astype(str), qdir / f"{digest[:12]}.parquet")   # tên theo hash -> idempotent
        processed.append(digest)

    all_new = pd.concat(frames, ignore_index=True)
    new_rows = all_new.drop_duplicates(subset=contract.key, keep="last")
    totals["duplicates_across_batches"] = len(all_new) - len(new_rows)
    updated = archived = 0
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        old_versions = _changed_versions(existing, new_rows, contract.key)
        if len(old_versions):
            archived = _archive_history(L, rtype, old_versions, contract.key, pending[-1][0][:12])
        updated = len(old_versions)
        merged = pd.concat([existing, new_rows], ignore_index=True)
    else:
        merged = new_rows
    before = len(merged)
    merged = merged.drop_duplicates(subset=contract.key, keep="last").sort_values(contract.key).reset_index(drop=True)
    atomic_write_parquet(merged, out_path)
    atomic_write_json(state_path, processed)      # ghi sau cùng: nếu sập giữa chừng thì lần sau xử lý lại, vẫn đúng
    return {"batches": len(pending), **totals, "duplicates_vs_existing": before - len(merged),
            "updated_rows": updated, "archived_versions": archived, "rows_total": len(merged)}


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


def _archive_history(L: Layout, rtype: str, old: pd.DataFrame, key: List[str], batch: str) -> int:
    """Lưu phiên bản cũ vào silver/history/<loại>.parquet (khử trùng theo khoá + nội dung nên chạy lại không nhân đôi)."""
    hdir = L.silver / "history"
    hdir.mkdir(parents=True, exist_ok=True)
    path = hdir / f"{rtype}.parquet"
    cols = [c for c in old.columns if c not in key]
    old = old.assign(row_hash=_row_hash(old, cols).values, superseded_by_batch=batch,
                     superseded_at=pd.Timestamp.now(tz="UTC"))
    if path.exists():
        old = pd.concat([pd.read_parquet(path), old], ignore_index=True)
    before = len(pd.read_parquet(path)) if path.exists() else 0
    old = old.drop_duplicates(subset=[*key, "row_hash"], keep="first")
    atomic_write_parquet(old, path)
    return len(old) - before


# ---------------------------------------------------------------- GOLD
def gold_sessions(ctx: Context) -> Dict:
    """Làm giàu phiên bằng tín hiệu review. Mỗi review khác nhau chỉ được phân tích MỘT lần (cache bền trên đĩa),
    nên đổi/thêm dữ liệu không phải gọi lại LLM cho review cũ."""
    import hashlib
    import json as _json
    from src.ai_extractor import ReviewSignalExtractor
    ext = ReviewSignalExtractor(use_llm=ctx.use_llm)
    mode = "llm:gpt-4o-mini" if ext.use_llm else "offline:" + hashlib.sha256(
        _json.dumps([ext.ISSUE_KEYWORDS, ext.POSITIVE_KEYWORDS], sort_keys=True).encode()).hexdigest()[:10]
    cache_path = ctx.layout.state / "review_signal_cache.json"
    cache: Dict[str, dict] = read_json(cache_path, {})

    df = pd.read_parquet(ctx.layout.silver / "session.parquet")
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
    df = pd.read_parquet(ctx.layout.gold / "sessions_features.parquet")
    atomic_write_parquet(analytics.region_summary(df), ctx.layout.gold / "region_summary.parquet")
    atomic_write_parquet(analytics.category_summary(df), ctx.layout.gold / "category_summary.parquet")
    return {"regions": df["region"].nunique(), "categories": df["category"].nunique()}


def gold_competitor_prices(ctx: Context) -> Dict:
    path = ctx.layout.silver / "price_observation.parquet"
    if not path.exists():
        return {"skipped": "no price_observation data"}
    df = pd.read_parquet(path)
    g = df.groupby(["product_code", "country", "currency"]).agg(
        n_stores=("store", "nunique"), min_price=("price", "min"), median_price=("price", "median"),
        max_price=("price", "max"), last_seen=("observed_at", "max")).reset_index()
    atomic_write_parquet(g, ctx.layout.gold / "competitor_prices.parquet")
    return {"groups": len(g)}
