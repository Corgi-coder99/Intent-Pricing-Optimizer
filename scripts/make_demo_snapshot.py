"""Write a small snapshot of the real EU/UK market tables to demo_data/ so the country-comparison page also works on a fresh
checkout or on a hosted demo (the pipeline's data/ folder is git-ignored). Run after `python -m src.pipeline run`.

Contains only aggregated/public product price data (Open Food Facts contributors, ODbL; ECB; Eurostat): no stores, no users."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import DATA_DIR  # noqa: E402
from src.pipeline.infra import Layout  # noqa: E402

src, dst = Layout(DATA_DIR), Layout(ROOT / "demo_data")
dst.ensure()
pd.read_parquet(src.gold / "market_margin.parquet").to_parquet(dst.gold / "market_margin.parquet", index=False)
pd.read_parquet(src.silver / "fx_rate.parquet").to_parquet(dst.silver / "fx_rate.parquet", index=False)
o = pd.read_parquet(src.silver / "price_observation.parquet")
cc = o.dropna(subset=["lat", "lon"]).groupby("country")[["lat", "lon"]].mean().reset_index()
slim = o.dropna(subset=["product_name"])[["product_code", "product_name", "country"]].drop_duplicates(["product_code", "country"]).merge(cc, on="country", how="left")
slim.to_parquet(dst.silver / "price_observation.parquet", index=False)
print("snapshot written to", ROOT / "demo_data")
