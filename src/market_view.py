"""Data for the EU/UK country-comparison dashboard page (reads gold/silver tables, writes nothing)."""
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.config import BASE_DIR, DATA_DIR
from src.pipeline.infra import Layout


def load_market(root: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """None if the pipeline has not built the `market_margin` table yet."""
    layout = Layout(root or DATA_DIR)
    gold = layout.gold / "market_margin.parquet"
    if not gold.exists() and root is None:       # fresh checkout / hosted demo: use the committed snapshot (scripts/make_demo_snapshot.py)
        layout = Layout(BASE_DIR / "demo_data")
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
            rate_date = str(g["rate_date"].iloc[0])[:10]
            age = (pd.Timestamp.now().normalize() - pd.Timestamp(rate_date)).days
            gbp = {"eur_per_unit": float(g["eur_per_unit"].iloc[0]), "rate_date": rate_date, "age_days": int(age)}
    return {"market": market, "names": names, "coords": coords, "gbp": gbp}


def _compared_rows(market: pd.DataFrame, only_confident: bool, countries: Optional[List[str]]) -> pd.DataFrame:
    """Rows of non-home countries that can be compared with the home country (valid price and a valid price gap)."""
    d = market[(market["status"] == "ok") & (market["country"] != market["home_country"])].dropna(subset=["vs_home_pct"])
    if only_confident:
        # both the compared row AND the home-country row must be reliable
        home_ok = set(market[(market["country"] == market["home_country"]) & (market["status"] == "ok")
                             & (market["confidence"] == "ok")]["product_code"])
        d = d[(d["confidence"] == "ok") & d["product_code"].isin(home_ok)]
    if countries:
        d = d[d["country"].isin(countries)]
    return d


def compared_product_count(market: pd.DataFrame, only_confident: bool = False, countries: Optional[List[str]] = None) -> int:
    """Products that appear in the country comparison chart (same definition as `country_summary`)."""
    return int(_compared_rows(market, only_confident, countries)["product_code"].nunique())


def country_summary(market: pd.DataFrame, only_confident: bool = False, countries: Optional[List[str]] = None) -> pd.DataFrame:
    """Per country (excluding the home country): number of comparable products, median price gap vs home, median scenario margin,
    sector margin (Eurostat)."""
    d = _compared_rows(market, only_confident, countries)
    if d.empty:
        return pd.DataFrame(columns=["country", "n_products", "median_vs_home_pct", "median_scenario_margin_pct",
                                     "sector_gross_margin_pct"])
    g = d.groupby("country").agg(n_products=("product_code", "nunique"), median_vs_home_pct=("vs_home_pct", "median"),
                                 median_scenario_margin_pct=("scenario_margin_pct", "median"),
                                 sector_gross_margin_pct=("sector_gross_margin_pct", "first")).reset_index()
    return g.round(2)


def comparable_products(market: pd.DataFrame, only_confident: bool = False,
                        countries: Optional[List[str]] = None) -> List[str]:
    """Product codes with valid prices in two or more countries (the selected countries plus the home country)."""
    d = market[market["status"] == "ok"].dropna(subset=["market_net_eur"])
    if countries is not None:
        d = d[d["country"].isin(set(countries) | set(d["home_country"].unique()))]
    if only_confident:
        d = d[d["confidence"] == "ok"]
    n = d.groupby("product_code")["country"].nunique()
    codes = n[n >= 2].index
    home = set(d[d["country"] == d["home_country"]]["product_code"])
    obs = d.groupby("product_code")["n_obs"].sum()
    # products with home-country data and the most observations come first (the '% vs home' column is only meaningful then)
    return sorted(codes, key=lambda c: (c not in home, -float(obs.get(c, 0)), c))


def product_table(market: pd.DataFrame, code: str) -> pd.DataFrame:
    cols = ["country", "market_net_eur", "vs_home_pct", "scenario_margin_pct", "sector_gross_margin_pct", "n_obs",
            "n_stores", "confidence"]
    return market[(market["product_code"] == code) & (market["status"] == "ok")][cols].sort_values("country").reset_index(drop=True)
