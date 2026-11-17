"""Export a self-contained, interactive HTML snapshot of the dashboard (no server needed, e.g. for GitHub Pages).

Everything is precomputed from the trained model and the gold data, so the numbers are the ones the Streamlit app shows
with its default settings. UI strings come from src/i18n.py, so English and French match the live app.

Run:  python -m scripts.export_static_demo  [output.html]
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import analytics  # noqa: E402
from src.agent_decision import PricingDecisionAgent  # noqa: E402
from src.config import PROCESSED_DATASET_PATH  # noqa: E402
from src.data_generator import PRODUCT_CATALOG  # noqa: E402
from src.i18n import category_label, complaint_label, t  # noqa: E402
from src.ml_pricing_model import DynamicPricingModel  # noqa: E402
from src.rag_engine import ReviewRAGEngine  # noqa: E402

KEYS = ["title", "subtitle", "product", "kpi_sessions", "kpi_conversion", "kpi_revenue", "kpi_price", "kpi_gain", "agent",
        "curve_title", "rev_100", "conv_pct", "revenue_eur", "price_axis", "vs_competitors", "our_price", "map_title",
        "region_chart", "sentiment_x100", "col_conversion", "col_sentiment", "col_revenue", "category_gap", "gap_axis",
        "complaints", "col_sessions", "language"]
EXTRA = {
    "note": {"en": "Static snapshot of the live app, simulated data, default settings. Sliders, region filter and the REST API need the real app: see the repository.",
             "fr": "Instantané statique de l'application, données simulées, réglages par défaut. Curseurs, filtre de région et API REST : voir l'application réelle dans le dépôt."},
    "repo": {"en": "Source code and documentation on GitHub", "fr": "Code source et documentation sur GitHub"},
    "discount": {"en": "discount", "fr": "remise"}, "markup": {"en": "markup", "fr": "hausse"},
}


def main(out: Path) -> None:
    df = pd.read_csv(PROCESSED_DATASET_PATH)
    model = DynamicPricingModel()
    model.load()
    rag = ReviewRAGEngine(df)
    agent = PricingDecisionAgent(use_llm=False)

    products = []
    for prod in PRODUCT_CATALOG:
        comp_name = next(iter(prod["competitors"]))
        comp_price = float(prod["competitors"][comp_name])
        hist = analytics.filter_sessions(df, product_id=prod["product_id"])
        sentiment = round(float(hist["ai_sentiment_score"].mean()), 2)
        base_price = float(prod["base_price"])
        context = {"base_price": base_price, "competitor_price": comp_price, "ai_sentiment_score": sentiment,
                   "pain_point_severity": round(max(0.05, 1.0 - sentiment), 2),
                   "session_views": float(hist["session_views"].mean()), "cart_additions_7d": float(hist["cart_additions_7d"].mean())}
        sim = model.simulate_price_curve(context)
        top = rag.retrieve_product_evidence(prod["product_id"], region=None)["top_complaints"]
        best = sim.loc[sim["expected_revenue_per_100_visits"].idxmax()]
        base_row = sim.iloc[(sim["offered_price"] - base_price).abs().argmin()]
        opt_price = float(best["offered_price"])
        scope = analytics.filter_sessions(df, category=analytics.ALL, product_id=prod["product_id"])
        ct = analytics.competitor_table(prod["product_id"], opt_price)
        dec = {lg: agent.recommend_action(prod["product_name"], base_price, sim, top, sentiment, lang=lg) for lg in ("en", "fr")}
        products.append({
            "name": prod["product_name"], "category": prod["category"], "comp_name": comp_name, "comp_price": comp_price,
            "curve": {"price": sim["offered_price"].round(2).tolist(), "rev": sim["expected_revenue_per_100_visits"].round(2).tolist(),
                      "conv": (sim["conversion_prob"] * 100).round(2).tolist()},
            "opt_price": opt_price, "discount_pct": float(best["discount_pct"]),
            "gain": float(best["expected_revenue_per_100_visits"] - base_row["expected_revenue_per_100_visits"]),
            "sessions": int(len(scope)), "conversion_rate": float(scope["converted"].mean()),
            "revenue": float((scope["offered_price"] * scope["converted"]).sum()),
            "competitors": [{"seller": r.seller, "ours": r.seller == analytics.OUR_LABEL, "price": float(r.price)} for r in ct.itertuples()],
            "summary": {lg: dec[lg]["executive_summary"] for lg in dec}, "strategy": {lg: dec[lg]["strategy_label"] for lg in dec},
        })

    reg = analytics.region_summary(df)
    gap = analytics.category_competitor_gap(df)
    cb = analytics.complaint_breakdown(df)
    data = {
        "strings": {k: {lg: t(k, lg) for lg in ("en", "fr")} for k in KEYS} | EXTRA,
        "products": products,
        "regions": reg[["region", "lat", "lon", "revenue", "conversion_rate", "avg_sentiment"]].round(4).to_dict("records"),
        "gap": [{"label": {lg: category_label(r.category, lg) for lg in ("en", "fr")}, "gap": round(float(r.gap_pct), 2)} for r in gap.itertuples()],
        "complaints": [{"label": {lg: complaint_label(r.complaint_category, lg) for lg in ("en", "fr")}, "sessions": int(r.sessions)} for r in cb.itertuples()],
    }
    html = (ROOT / "scripts" / "static_demo_template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print("wrote", out, round(out.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "static-demo" / "index.html")
