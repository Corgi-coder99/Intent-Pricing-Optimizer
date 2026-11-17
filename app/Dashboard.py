import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src import analytics
from src.agent_decision import PricingDecisionAgent, money, number
from src.analytics import ALL, OUR_LABEL
from src.config import MODEL_ARTIFACT_PATH, PROCESSED_DATASET_PATH
from src.data_generator import PRODUCT_CATALOG
from src.i18n import category_label, complaint_label, fr_typography, streamlit_lang, t
from src.ml_pricing_model import DynamicPricingModel
from src.rag_engine import ReviewRAGEngine
from src.train_pipeline import run_full_pipeline

st.set_page_config(page_title="Intent-Driven Pricing BI", page_icon="📈", layout="wide")
lang = streamlit_lang()

NAVY, BLUE, ORANGE, RED, GREEN, GREY = "#1f3a5f", "#2f7fc1", "#f39c12", "#d64545", "#2e9e6b", "#8a96a8"

st.markdown(
    f"""
<style>
.block-container {{padding-top: 3.2rem; padding-bottom: 1rem; max-width: 1500px;}}
.kpi {{border-radius: 8px; padding: 14px 10px; overflow: hidden; color: #fff; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.18);}}
.kpi .v {{font-size: clamp(1.05rem, 1.7vw, 1.9rem); white-space: nowrap; font-weight: 700; line-height: 1.1;}}
.kpi .l {{font-size: clamp(.64rem, .9vw, .82rem); opacity: .92; margin-top: 2px; overflow-wrap: normal; word-break: normal;}}
.panel {{background:#fff; border:1px solid #e3e8ef; border-radius:8px; padding:6px 10px 2px 10px;}}
.ptitle {{font-weight:600; color:{NAVY}; font-size:.95rem; margin:6px 0 0 4px;}}
.agent {{border-left:5px solid {BLUE}; background:#eef5fc; padding:12px 16px; border-radius:6px; color:#1b2a3d;}}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def load_system():
    if not PROCESSED_DATASET_PATH.exists() or not MODEL_ARTIFACT_PATH.exists():
        run_full_pipeline(use_llm=False)
    df = pd.read_csv(PROCESSED_DATASET_PATH)
    model = DynamicPricingModel()
    model.load()
    return df, model, ReviewRAGEngine(df), PricingDecisionAgent(use_llm=False)


df, model, rag, agent = load_system()


def kpi(label, value, color, sub=""):
    sub_html = f'<div class="l">{sub}</div>' if sub else ""
    return f'<div class="kpi" style="background:{color}"><div class="v">{value}</div><div class="l">{label}</div>{sub_html}</div>'


def gauge(title, value, lo, hi, fmt, steps, color=BLUE):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=value, number={"valueformat": fmt, "font": {"size": 26}},
        title={"text": title, "font": {"size": 12}},
        gauge={"axis": {"range": [lo, hi]}, "bar": {"color": color}, "steps": steps},
    ))
    fig.update_layout(height=190, margin=dict(l=28, r=28, t=45, b=5))
    if lang == "fr":
        fig.update_layout(separators=", ")      # decimal comma
    return fig


def show(fig):
    """Render a chart; French charts use a decimal comma and a space as thousands separator."""
    if lang == "fr":
        fig.update_layout(separators=", ")
    st.plotly_chart(fig, width="stretch")


def loc(text: str) -> str:
    """French typography (non-breaking spaces) for strings composed in the app; English untouched."""
    return fr_typography(text) if lang == "fr" else text


def pct(value: float, signed: bool = False) -> str:
    sign = ("+" if value >= 0 else "−") if signed else ""
    return f"{sign}{number(abs(value) if signed else value, lang)}" + ("\u00a0%" if lang == "fr" else "%")


def signed_money(value: float, decimals: int = 0) -> str:
    return ("+" if value >= 0 else "−") + money(abs(value), lang, decimals)


# ---------------- FILTER BAR ----------------
st.markdown(f"<h3 style='color:{NAVY};margin:0'>📈 {t('title', lang)}</h3>", unsafe_allow_html=True)
st.caption(t("subtitle", lang))

f1, f2, f3, f4 = st.columns(4)
category = f1.selectbox(t("category", lang), [ALL] + sorted(df["category"].unique().tolist()),
                        format_func=lambda c: t("all", lang) if c == ALL else category_label(c, lang))
prods = [p for p in PRODUCT_CATALOG if category == ALL or p["category"] == category]
prod_name = f2.selectbox(t("product", lang), [p["product_name"] for p in prods])
prod = next(p for p in prods if p["product_name"] == prod_name)
region = f3.selectbox(t("region", lang), [ALL] + sorted(df["region"].unique().tolist()),
                      format_func=lambda r: t("all", lang) if r == ALL else r)
comp_name = f4.selectbox(t("competitor", lang), list(prod["competitors"].keys()))

with st.expander(t("market_sim", lang), expanded=False):
    s1, s2, s3 = st.columns(3)
    base_price = s1.slider(t("list_price", lang), round(prod["base_price"] * 0.7, 2), round(prod["base_price"] * 1.3, 2),
                           float(prod["base_price"]), 1.0)
    comp_price = s2.slider(t("competitor_price", lang, name=comp_name), round(prod["competitors"][comp_name] * 0.7, 2),
                           round(prod["competitors"][comp_name] * 1.3, 2), float(prod["competitors"][comp_name]), 1.0)
    p_hist = analytics.filter_sessions(df, product_id=prod["product_id"], region=region)
    default_sent = float(p_hist["ai_sentiment_score"].mean())
    sentiment = s3.slider(t("sentiment_override", lang), 0.05, 0.95, round(default_sent, 2), 0.05)

severity = round(max(0.05, 1.0 - sentiment), 2)
context = {
    "base_price": base_price, "competitor_price": comp_price,
    "ai_sentiment_score": sentiment, "pain_point_severity": severity,
    "session_views": float(p_hist["session_views"].mean()), "cart_additions_7d": float(p_hist["cart_additions_7d"].mean()),
}
sim = model.simulate_price_curve(context)
evidence = rag.retrieve_product_evidence(prod["product_id"], region=None if region == ALL else region)
decision = agent.recommend_action(prod_name, base_price, sim, evidence["top_complaints"], sentiment, lang=lang)

best = sim.loc[sim["expected_revenue_per_100_visits"].idxmax()]
base_row = sim.iloc[(sim["offered_price"] - base_price).abs().argmin()]
opt_price, opt_conv = float(best["offered_price"]), float(best["conversion_prob"])
rev_lift = float(best["expected_revenue_per_100_visits"] - base_row["expected_revenue_per_100_visits"])

scope = analytics.filter_sessions(df, category=category, region=region)
n_sessions, conv_rate = len(scope), scope["converted"].mean()
revenue = float((scope["offered_price"] * scope["converted"]).sum())

# ---------------- KPI CARDS ----------------
k = st.columns(5)
k[0].markdown(kpi(t("kpi_sessions", lang), f"{n_sessions:,}".replace(",", " " if lang == "fr" else ","), NAVY), unsafe_allow_html=True)
k[1].markdown(kpi(t("kpi_conversion", lang), number(conv_rate * 100, lang) + (" %" if lang == "fr" else "%"), BLUE), unsafe_allow_html=True)
k[2].markdown(kpi(t("kpi_revenue", lang), money(revenue, lang, 0), GREEN), unsafe_allow_html=True)
disc_pct = float(best["discount_pct"])
k[3].markdown(kpi(t("kpi_price", lang), decision["recommended_price"], ORANGE,
                  sub=t("kpi_discount" if disc_pct >= 0 else "kpi_markup", lang, discount=pct(abs(disc_pct)))), unsafe_allow_html=True)
k[4].markdown(kpi(t("kpi_gain", lang), signed_money(rev_lift), RED if rev_lift < 0 else GREEN), unsafe_allow_html=True)

st.write("")
# ---------------- GAUGES ----------------
avg_comp = sum(prod["competitors"].values()) / len(prod["competitors"])
g = st.columns(4)
g[0].plotly_chart(gauge(t("g_conversion", lang), opt_conv * 100, 0, 100, ".1f",
                        [{"range": [0, 30], "color": "#f6d5d5"}, {"range": [30, 55], "color": "#fbe9c6"}, {"range": [55, 100], "color": "#d4eedf"}]))
g[1].plotly_chart(gauge(t("g_sentiment", lang), sentiment, 0, 1, ".2f",
                        [{"range": [0, .45], "color": "#f6d5d5"}, {"range": [.45, .7], "color": "#fbe9c6"}, {"range": [.7, 1], "color": "#d4eedf"}], GREEN))
g[2].plotly_chart(gauge(t("g_price_ratio", lang), opt_price / avg_comp, 0.6, 1.4, ".2f",
                        [{"range": [0.6, 0.95], "color": "#d4eedf"}, {"range": [0.95, 1.05], "color": "#fbe9c6"}, {"range": [1.05, 1.4], "color": "#f6d5d5"}], ORANGE))
g[3].plotly_chart(gauge(t("g_pain", lang), severity, 0, 1, ".2f",
                        [{"range": [0, .3], "color": "#d4eedf"}, {"range": [.3, .6], "color": "#fbe9c6"}, {"range": [.6, 1], "color": "#f6d5d5"}], RED))

st.markdown(f"<div class='agent'><b>🤖 {t('agent', lang)} — {decision['strategy_label']}{loc(' :') if lang == 'fr' else ':'}</b> {decision['executive_summary']}</div>", unsafe_allow_html=True)
st.write("")

# ---------------- ROW: CURVE + COMPETITORS ----------------
c1, c2 = st.columns([1.5, 1])
with c1:
    st.markdown(f"<div class='ptitle'>{t('curve_title', lang)}</div>", unsafe_allow_html=True)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=sim["offered_price"], y=sim["expected_revenue_per_100_visits"], name=t("rev_100", lang),
                         marker_color=[ORANGE if p == opt_price else "#b9d3ea" for p in sim["offered_price"]]), secondary_y=False)
    fig.add_trace(go.Scatter(x=sim["offered_price"], y=sim["conversion_prob"] * 100, name=t("conv_pct", lang),
                             mode="lines+markers", line=dict(color=NAVY, width=3)), secondary_y=True)
    fig.add_vline(x=comp_price, line_dash="dash", line_color=RED, annotation_text=f"{comp_name} {money(comp_price, lang, 2)}")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=80, b=10), legend=dict(orientation="h", yanchor="bottom", y=1.02),
                      plot_bgcolor="#fff", bargap=0.15, xaxis_title=t("price_axis", lang))
    fig.update_yaxes(title_text=t("revenue_eur", lang), secondary_y=False)
    fig.update_yaxes(title_text=t("conv_pct", lang), secondary_y=True)
    show(fig)
with c2:
    st.markdown(f"<div class='ptitle'>{t('vs_competitors', lang)}</div>", unsafe_allow_html=True)
    ct = analytics.competitor_table(prod["product_id"], opt_price)
    ct["label"] = ct["seller"].map(lambda s: t("our_price", lang) if s == OUR_LABEL else s)
    fig = px.bar(ct, x="price", y="label", orientation="h", text=ct["price"].map(lambda v: money(v, lang)),
                 color=ct["seller"].eq(OUR_LABEL), color_discrete_map={True: ORANGE, False: BLUE})
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10), showlegend=False, plot_bgcolor="#fff",
                      yaxis_title=None, xaxis_title=t("price_axis", lang))
    show(fig)

# ---------------- ROW: MAP + REGION BARS ----------------
m1, m2 = st.columns([1, 1.2])
reg_df = analytics.region_summary(analytics.filter_sessions(df, category=category, product_id=prod["product_id"]))
with m1:
    st.markdown(f"<div class='ptitle'>{t('map_title', lang)}</div>", unsafe_allow_html=True)
    fig = px.scatter_geo(reg_df, lat="lat", lon="lon", size="revenue", color="conversion_rate", hover_name="region",
                         hover_data={"lat": False, "lon": False, "revenue": ":,.0f", "conversion_rate": ":.1%", "avg_sentiment": ":.2f"},
                         color_continuous_scale="RdYlGn", size_max=38)
    fig.update_geos(scope="europe", lataxis_range=[41, 52], lonaxis_range=[-5.5, 9.5], showcountries=True,
                    countrycolor="#c9d1dc", showland=True, landcolor="#f4f6f9", showocean=True, oceancolor="#e8f1fa", resolution=50)
    fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), coloraxis_colorbar=dict(title="Conv."))
    show(fig)
with m2:
    st.markdown(f"<div class='ptitle'>{t('region_chart', lang)}</div>", unsafe_allow_html=True)
    rs = reg_df.sort_values("revenue")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=rs["region"], y=rs["revenue"], name=t("revenue_eur", lang), marker_color=BLUE), secondary_y=False)
    fig.add_trace(go.Scatter(x=rs["region"], y=rs["conversion_rate"] * 100, name=t("conv_pct", lang), mode="lines+markers",
                             line=dict(color=ORANGE, width=3)), secondary_y=True)
    fig.add_trace(go.Scatter(x=rs["region"], y=rs["avg_sentiment"] * 100, name=t("sentiment_x100", lang), mode="lines+markers",
                             line=dict(color=GREEN, width=2, dash="dot")), secondary_y=True)
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=80, b=10), legend=dict(orientation="h", yanchor="bottom", y=1.02), plot_bgcolor="#fff")
    show(fig)

# ---------------- ROW: CATEGORY + COMPLAINTS + RAG ----------------
r1, r2, r3 = st.columns([1.1, 0.9, 1.1])
all_cat = analytics.category_summary(df if region == ALL else analytics.filter_sessions(df, region=region))
gap = analytics.category_competitor_gap(df)
with r1:
    st.markdown(f"<div class='ptitle'>{t('category_gap', lang)}</div>", unsafe_allow_html=True)
    gap = gap.assign(label=gap["category"].map(lambda c: category_label(c, lang)))
    fig = px.bar(gap, x="label", y="gap_pct", text=gap["gap_pct"].map(lambda v: pct(v, signed=True)),
                 color="gap_pct", color_continuous_scale="RdYlGn_r")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), coloraxis_showscale=False, plot_bgcolor="#fff",
                      xaxis_title=None, yaxis_title=t("gap_axis", lang))
    show(fig)
with r2:
    st.markdown(f"<div class='ptitle'>{t('complaints', lang)}</div>", unsafe_allow_html=True)
    cb = analytics.complaint_breakdown(scope)
    cb["label"] = cb["complaint_category"].map(lambda c: complaint_label(c, lang))
    fig = px.pie(cb, names="label", values="sessions", hole=0.45, color_discrete_sequence=[GREEN, ORANGE, RED, BLUE])
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.1))
    show(fig)
with r3:
    st.markdown(f"<div class='ptitle'>🔍 {t('voc', lang)}</div>", unsafe_allow_html=True)
    friction = ", ".join(complaint_label(c, lang) for c in evidence["top_complaints"]) or t("none_word", lang)
    st.caption(loc(f"{t('friction', lang)} : `{friction}`") if lang == "fr" else f"{t('friction', lang)}: `{friction}`")
    for item in evidence["evidence_quotes"]:
        with st.container(border=True):
            st.markdown(f"*\"{item['review_text']}\"*")
            st.caption(loc(f"{t('tag', lang)} : **{complaint_label(item['complaint_category'], lang)}** · "
                           f"{t('sentiment', lang)} `{number(item['sentiment_score'], lang, 2)}`") if lang == "fr" else
                       f"{t('tag', lang)}: **{complaint_label(item['complaint_category'], lang)}** · "
                       f"{t('sentiment', lang)} `{item['sentiment_score']:.2f}`")

st.markdown(f"<div class='ptitle'>{t('summary_table', lang)}</div>", unsafe_allow_html=True)
show = all_cat.assign(category=all_cat["category"].map(lambda c: category_label(c, lang))).rename(columns={
    "category": t("col_category", lang), "sessions": t("col_sessions", lang), "conversion_rate": t("col_conversion", lang),
    "avg_sentiment": t("col_sentiment", lang), "avg_severity": t("col_pain", lang), "revenue": t("col_revenue", lang)})
st.dataframe(show.style.format({t("col_conversion", lang): "{:.1%}", t("col_sentiment", lang): "{:.2f}",
                                t("col_pain", lang): "{:.2f}", t("col_revenue", lang): "{:,.0f}"}),
             width="stretch", hide_index=True)
