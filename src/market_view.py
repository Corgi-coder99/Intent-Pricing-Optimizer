"""Dữ liệu cho trang dashboard so sánh quốc gia EU/UK (đọc bảng gold/silver, không ghi gì)."""
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.config import DATA_DIR
from src.pipeline.infra import Layout


def load_market(root: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """None nếu pipeline chưa tạo bảng `market_margin`."""
    layout = Layout(root or DATA_DIR)
    gold = layout.gold / "market_margin.parquet"
    if not gold.exists():
        return None
    market = pd.read_parquet(gold)
    names: Dict[str, str] = {}
    coords = pd.DataFrame(columns=["country", "lat", "lon"])
    obs = layout.silver / "price_observation.parquet"
    if obs.exists():
        o = pd.read_parquet(obs)
        if "product_name" in o:
            nm = o.dropna(subset=["product_name"]).drop_duplicates("product_code")
            names = dict(zip(nm["product_code"], nm["product_name"]))
        if {"lat", "lon"} <= set(o.columns):
            coords = o.dropna(subset=["lat", "lon"]).groupby("country")[["lat", "lon"]].mean().reset_index()
    fx = layout.silver / "fx_rate.parquet"
    gbp = None
    if fx.exists():
        f = pd.read_parquet(fx)
        g = f[f["currency"] == "GBP"].sort_values("rate_date").tail(1)
        if len(g):
            gbp = {"eur_per_unit": float(g["eur_per_unit"].iloc[0]), "rate_date": str(g["rate_date"].iloc[0])[:10]}
    return {"market": market, "names": names, "coords": coords, "gbp": gbp}


def country_summary(market: pd.DataFrame, only_confident: bool = False, countries: Optional[List[str]] = None) -> pd.DataFrame:
    """Theo quốc gia (trừ nước nhà): số sản phẩm so sánh được, chênh lệch giá trung vị so với nước nhà, biên giả định trung vị,
    biên ngành (Eurostat)."""
    d = market[(market["status"] == "ok") & (market["country"] != market["home_country"])].dropna(subset=["vs_home_pct"])
    if only_confident:
        d = d[d["confidence"] == "ok"]
    if countries:
        d = d[d["country"].isin(countries)]
    if d.empty:
        return pd.DataFrame(columns=["country", "n_products", "median_vs_home_pct", "median_scenario_margin_pct",
                                     "sector_gross_margin_pct"])
    g = d.groupby("country").agg(n_products=("product_code", "nunique"), median_vs_home_pct=("vs_home_pct", "median"),
                                 median_scenario_margin_pct=("scenario_margin_pct", "median"),
                                 sector_gross_margin_pct=("sector_gross_margin_pct", "first")).reset_index()
    return g.round(2)


def comparable_products(market: pd.DataFrame, only_confident: bool = False) -> List[str]:
    """Mã sản phẩm có giá hợp lệ ở từ 2 quốc gia trở lên."""
    d = market[market["status"] == "ok"].dropna(subset=["market_net_eur"])
    if only_confident:
        d = d[d["confidence"] == "ok"]
    n = d.groupby("product_code")["country"].nunique()
    codes = n[n >= 2].index
    home = set(d[d["country"] == d["home_country"]]["product_code"])
    obs = d.groupby("product_code")["n_obs"].sum()
    # sản phẩm có dữ liệu nước nhà và nhiều quan sát nhất lên đầu (cột "% vs nước nhà" mới có nghĩa)
    return sorted(codes, key=lambda c: (c not in home, -float(obs.get(c, 0)), c))


def product_table(market: pd.DataFrame, code: str) -> pd.DataFrame:
    cols = ["country", "market_net_eur", "vs_home_pct", "scenario_margin_pct", "sector_gross_margin_pct", "n_obs",
            "n_stores", "confidence"]
    return market[(market["product_code"] == code) & (market["status"] == "ok")][cols].sort_values("country").reset_index(drop=True)
