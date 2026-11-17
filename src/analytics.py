"""Aggregations feeding the BI dashboard (region / category / competitor views)."""
from typing import Dict, Optional
import pandas as pd
from src.data_generator import PRODUCT_CATALOG


def filter_sessions(df: pd.DataFrame, category: Optional[str] = None,
                    product_id: Optional[str] = None, region: Optional[str] = None) -> pd.DataFrame:
    out = df
    if category and category != "Tất cả":
        out = out[out["category"] == category]
    if product_id:
        out = out[out["product_id"] == product_id]
    if region and region != "Tất cả":
        out = out[out["region"] == region]
    return out


def _agg(g: pd.core.groupby.DataFrameGroupBy) -> pd.DataFrame:
    res = g.agg(
        sessions=("session_id", "count"),
        conversion_rate=("converted", "mean"),
        avg_sentiment=("ai_sentiment_score", "mean"),
        avg_severity=("pain_point_severity", "mean"),
    )
    revenue = g.apply(lambda x: float((x["offered_price"] * x["converted"]).sum()), include_groups=False)
    res["revenue"] = revenue
    return res.reset_index()


def region_summary(df: pd.DataFrame) -> pd.DataFrame:
    out = _agg(df.groupby(["region", "lat", "lon"]))
    return out


def category_summary(df: pd.DataFrame) -> pd.DataFrame:
    return _agg(df.groupby("category"))


def complaint_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    t = df["complaint_category"].value_counts().rename_axis("complaint_category").reset_index(name="sessions")
    return t


def competitor_table(product_id: str, our_price: float) -> pd.DataFrame:
    """Giá của ta so với 3 đối thủ của sản phẩm, kèm chênh lệch %."""
    prod = next(p for p in PRODUCT_CATALOG if p["product_id"] == product_id)
    rows = [{"seller": "Giá đề xuất (ta)", "price": our_price}]
    rows += [{"seller": name, "price": price} for name, price in prod["competitors"].items()]
    t = pd.DataFrame(rows)
    t["vs_ours_pct"] = (t["price"] / our_price - 1.0) * 100.0
    return t


def category_competitor_gap(df: pd.DataFrame) -> pd.DataFrame:
    """Chênh lệch giá trung bình của ta so với đối thủ chính, theo ngành hàng."""
    cat = df.groupby("category").agg(our_price=("offered_price", "mean"), competitor_price=("competitor_price", "mean"))
    cat["gap_pct"] = (cat["our_price"] / cat["competitor_price"] - 1.0) * 100.0
    return cat.reset_index()


def product_options() -> Dict[str, str]:
    return {p["product_name"]: p["product_id"] for p in PRODUCT_CATALOG}
