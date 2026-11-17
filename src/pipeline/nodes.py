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

    new_rows = pd.concat(frames, ignore_index=True)
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        merged = pd.concat([existing, new_rows], ignore_index=True)
    else:
        merged = new_rows
    before = len(merged)
    merged = merged.drop_duplicates(subset=contract.key, keep="last").sort_values(contract.key).reset_index(drop=True)
    atomic_write_parquet(merged, out_path)
    atomic_write_json(state_path, processed)      # ghi sau cùng: nếu sập giữa chừng thì lần sau xử lý lại, vẫn đúng
    return {"batches": len(pending), **totals, "duplicates_vs_existing": before - len(merged), "rows_total": len(merged)}


# ---------------------------------------------------------------- GOLD
def gold_sessions(ctx: Context) -> Dict:
    from src.ai_extractor import ReviewSignalExtractor
    df = pd.read_parquet(ctx.layout.silver / "session.parquet")
    df = df.rename(columns={})   # giữ tên cột silver
    enriched = ReviewSignalExtractor(use_llm=ctx.use_llm).enrich_dataframe(df)
    atomic_write_parquet(enriched, ctx.layout.gold / "sessions_features.parquet")
    return {"rows": len(enriched)}


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
