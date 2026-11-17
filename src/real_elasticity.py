"""Estimate the price elasticity of demand from REAL transactions (Online Retail II, UCI, CC BY 4.0) to check the assumptions
of the simulator before it is used in the model.

Data: product x week (units, quantity-weighted average price) from the `clean_lines` table of the online-retail-insights
project (read-only). Method: log q = a_i + g_t + e * log p + u with product (a_i) and week (g_t) fixed effects,
estimated by Frisch-Waugh (alternating demeaning); 95% confidence interval by bootstrap over products.

LIMITS (important): prices do not change randomly (promotions coincide with season/demand, wholesale tiered pricing) so
this is an OBSERVED elasticity, not a causal one; read it as a plausible range, not an exact figure.
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

# Set ONLINE_RETAIL_DB to the warehouse of the online-retail-insights project; by default look for it next to this repository.
DEFAULT_DB = Path(os.getenv("ONLINE_RETAIL_DB") or (Path(__file__).resolve().parent.parent.parent / "online-retail-insights" / "data" / "warehouse.duckdb"))
EU_OTHER = ("Austria", "Belgium", "Cyprus", "Czech Republic", "Denmark", "EIRE", "Finland", "France", "Germany", "Greece",
            "Italy", "Lithuania", "Malta", "Netherlands", "Poland", "Portugal", "Spain", "Sweden")


def load_weekly(db_path: Path = DEFAULT_DB, group: str = "all") -> pd.DataFrame:
    """Product x week. group: all | uk | eu (EU countries other than the UK). Opens the database READ-ONLY."""
    import duckdb
    where = {"all": "1=1", "uk": "country = 'United Kingdom'",
             "eu": "country IN (" + ",".join(f"'{c}'" for c in EU_OTHER) + ")"}[group]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(f"""
            SELECT stock_code AS product, date_trunc('week', invoice_date) AS week,
                   SUM(quantity)::DOUBLE AS units, SUM(quantity * price) / SUM(quantity) AS price,
                   COUNT(DISTINCT invoice) AS invoices
            FROM clean_lines
            WHERE line_type = 'sale' AND quantity > 0 AND price > 0 AND {where}
            GROUP BY 1, 2""").df()
    finally:
        con.close()


def quality_gate(df: pd.DataFrame, min_weeks: int = 30, min_price_cv: float = 0.05, min_distinct_prices: int = 3,
                 min_invoices: int = 2) -> pd.DataFrame:
    """Keep only products with enough observations AND price variation (without variation the elasticity cannot be estimated)."""
    d = df[df["invoices"] >= min_invoices].copy()                          # weeks with a single invoice: too noisy
    med = d.groupby("product")["price"].transform("median")
    d = d[(d["price"] >= 0.2 * med) & (d["price"] <= 5.0 * med)]            # drop mistyped prices (extreme relative to the median)
    g = d.groupby("product")["price"]
    stats = pd.DataFrame({"weeks": g.size(), "cv": g.std() / g.mean(), "distinct": g.nunique()})
    keep = stats[(stats["weeks"] >= min_weeks) & (stats["cv"] >= min_price_cv) & (stats["distinct"] >= min_distinct_prices)].index
    return d[d["product"].isin(keep)].reset_index(drop=True)


def _demean_two_way(v: np.ndarray, prod: np.ndarray, week: np.ndarray, iters: int = 30) -> np.ndarray:
    x = v.astype(float).copy()
    for _ in range(iters):
        for key in (prod, week):
            s = pd.Series(x).groupby(key).transform("mean").to_numpy()
            x = x - s
    return x


def estimate_elasticity(df: pd.DataFrame, n_boot: int = 300, seed: int = 42) -> Dict[str, object]:
    """Pooled elasticity (product + week fixed effects), 95% bootstrap CI over products, and the per-product median."""
    if df.empty:
        raise ValueError("No data after quality gate")
    prod = pd.factorize(df["product"])[0]
    week = pd.factorize(df["week"])[0]
    y, x = np.log(df["units"].to_numpy()), np.log(df["price"].to_numpy())
    yt, xt = _demean_two_way(y, prod, week), _demean_two_way(x, prod, week)
    eps = float((xt * yt).sum() / (xt * xt).sum())

    # bootstrap over products on the demeaned residuals (an approximation, acceptable for the confidence interval)
    n_p = prod.max() + 1
    num = np.bincount(prod, weights=xt * yt, minlength=n_p)
    den = np.bincount(prod, weights=xt * xt, minlength=n_p)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n_p, n_p)
        d = den[idx].sum()
        if d > 0:
            boots.append(num[idx].sum() / d)
    lo, hi = np.percentile(boots, [2.5, 97.5])

    per_product = []
    for _, g in df.groupby("product"):
        gx, gy = np.log(g["price"].to_numpy()), np.log(g["units"].to_numpy())
        vx = gx - gx.mean()
        if (vx ** 2).sum() > 0:
            per_product.append(float((vx * (gy - gy.mean())).sum() / (vx ** 2).sum()))
    pp = np.array(per_product)
    return {"elasticity": round(eps, 3), "ci95": [round(float(lo), 3), round(float(hi), 3)], "n_products": int(n_p),
            "n_rows": int(len(df)), "per_product_median": round(float(np.median(pp)), 3),
            "share_negative": round(float((pp < 0).mean()), 3)}


def simulator_implied_elasticity(contexts: Optional[Dict[str, dict]] = None) -> Dict[str, float]:
    """Elasticity of CONVERSION PROBABILITY to price implied by the trained model (simulated data) around the base price."""
    from src.ml_pricing_model import DynamicPricingModel
    m = DynamicPricingModel()
    m.load()
    base = {"base_price": 100.0, "competitor_price": 100.0, "session_views": 4.0, "cart_additions_7d": 25.0}
    contexts = contexts or {"no_complaints (sentiment 0.95)": {"ai_sentiment_score": 0.95, "pain_point_severity": 0.05},
                            "average (sentiment 0.5)": {"ai_sentiment_score": 0.5, "pain_point_severity": 0.5},
                            "many_complaints (sentiment 0.1)": {"ai_sentiment_score": 0.1, "pain_point_severity": 0.9}}
    out = {}
    for name, ctx in contexts.items():
        curve = m.simulate_price_curve({**base, **ctx})
        near = curve.iloc[5:10]                                  # around the base price (about -7.5% .. -2.5%)
        lp, lc = np.log(near["offered_price"].to_numpy()), np.log(near["conversion_prob"].to_numpy())
        out[name] = round(float(np.polyfit(lp, lc, 1)[0]), 3)
    return out


def main(argv: Optional[list] = None) -> int:
    if not DEFAULT_DB.exists():
        print(f"ERROR: warehouse not found: {DEFAULT_DB} (set the ONLINE_RETAIL_DB environment variable to the path of warehouse.duckdb)", file=sys.stderr)
        return 2
    report: Dict[str, object] = {"source": "Online Retail II (UCI, CC BY 4.0), qua online-retail-insights/clean_lines"}
    for group in ("all", "uk", "eu"):
        raw = load_weekly(DEFAULT_DB, group)
        gated = quality_gate(raw)
        report[group] = {"weekly_rows_before_gate": len(raw), "products_before_gate": int(raw["product"].nunique()),
                         **(estimate_elasticity(gated) if len(gated) else {"error": "no data after gate"})}
    try:
        report["simulator_implied_conversion_elasticity"] = simulator_implied_elasticity()
    except FileNotFoundError:
        report["simulator_implied_conversion_elasticity"] = "model not trained"
    out = Path(__file__).resolve().parent.parent / "data" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "real_elasticity.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
