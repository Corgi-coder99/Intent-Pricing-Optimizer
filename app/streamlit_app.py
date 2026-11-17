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
from src.agent_decision import PricingDecisionAgent
from src.config import MODEL_ARTIFACT_PATH, PROCESSED_DATASET_PATH
from src.data_generator import PRODUCT_CATALOG
from src.ml_pricing_model import DynamicPricingModel
from src.rag_engine import ReviewRAGEngine
from src.train_pipeline import run_full_pipeline

st.set_page_config(page_title="Intent-Driven Pricing BI", page_icon="📈", layout="wide")

NAVY, BLUE, ORANGE, RED, GREEN, GREY = "#1f3a5f", "#2f7fc1", "#f39c12", "#d64545", "#2e9e6b", "#8a96a8"

st.markdown(
    f"""
<style>
.block-container {{padding-top: 3.2rem; padding-bottom: 1rem; max-width: 1500px;}}
.kpi {{border-radius: 8px; padding: 14px 16px; color: #fff; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.18);}}
.kpi .v {{font-size: clamp(1.05rem, 1.7vw, 1.9rem); white-space: nowrap; font-weight: 700; line-height: 1.1;}}
.kpi .l {{font-size: .82rem; opacity: .92; margin-top: 2px;}}
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


def kpi(label, value, color):
    return f'<div class="kpi" style="background:{color}"><div class="v">{value}</div><div class="l">{label}</div></div>'


def gauge(title, value, lo, hi, fmt, steps, color=BLUE):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=value, number={"valueformat": fmt, "font": {"size": 26}},
        title={"text": title, "font": {"size": 13}},
        gauge={"axis": {"range": [lo, hi]}, "bar": {"color": color}, "steps": steps},
    ))
    fig.update_layout(height=190, margin=dict(l=15, r=15, t=40, b=5))
    return fig


# ---------------- FILTER BAR ----------------
st.markdown(f"<h3 style='color:{NAVY};margin:0'>📈 Intent-Driven Pricing &amp; Conversion — BI Console</h3>", unsafe_allow_html=True)
st.caption("AI (NLP review mining) → Data Science (LightGBM price elasticity) → E-commerce (pricing agent). Dữ liệu mô phỏng.")

f1, f2, f3, f4 = st.columns(4)
categories = ["Tất cả"] + sorted(df["category"].unique().tolist())
category = f1.selectbox("Ngành hàng", categories)
prods = [p for p in PRODUCT_CATALOG if category == "Tất cả" or p["category"] == category]
prod_name = f2.selectbox("Sản phẩm", [p["product_name"] for p in prods])
prod = next(p for p in prods if p["product_name"] == prod_name)
region = f3.selectbox("Khu vực", ["Tất cả"] + sorted(df["region"].unique().tolist()))
comp_name = f4.selectbox("Đối thủ so sánh", list(prod["competitors"].keys()))

with st.expander("Giả lập thị trường (kéo để thử)", expanded=False):
    s1, s2, s3 = st.columns(3)
    base_price = s1.slider("Giá niêm yết ($)", round(prod["base_price"] * 0.7, 2), round(prod["base_price"] * 1.3, 2),
                           float(prod["base_price"]), 1.0)
    comp_price = s2.slider(f"Giá đối thủ {comp_name} ($)", round(prod["competitors"][comp_name] * 0.7, 2),
                           round(prod["competitors"][comp_name] * 1.3, 2), float(prod["competitors"][comp_name]), 1.0)
    p_hist = analytics.filter_sessions(df, product_id=prod["product_id"], region=region)
    default_sent = float(p_hist["ai_sentiment_score"].mean())
    sentiment = s3.slider("AI Sentiment (override)", 0.05, 0.95, round(default_sent, 2), 0.05)

severity = round(max(0.05, 1.0 - sentiment), 2)
context = {
    "base_price": base_price, "competitor_price": comp_price,
    "ai_sentiment_score": sentiment, "pain_point_severity": severity,
    "session_views": float(p_hist["session_views"].mean()), "cart_additions_7d": float(p_hist["cart_additions_7d"].mean()),
}
sim = model.simulate_price_curve(context)
evidence = rag.retrieve_product_evidence(prod["product_id"], region=None if region == "Tất cả" else region)
decision = agent.recommend_action(prod_name, base_price, sim, evidence["top_complaints"], sentiment)

best = sim.loc[sim["expected_revenue_per_100_visits"].idxmax()]
base_row = sim.iloc[(sim["offered_price"] - base_price).abs().argmin()]
opt_price, opt_conv = float(best["offered_price"]), float(best["conversion_prob"])
rev_lift = float(best["expected_revenue_per_100_visits"] - base_row["expected_revenue_per_100_visits"])

scope = analytics.filter_sessions(df, category=category, region=region)
n_sessions, conv_rate = len(scope), scope["converted"].mean()
revenue = float((scope["offered_price"] * scope["converted"]).sum())

# ---------------- KPI CARDS ----------------
k = st.columns(5)
k[0].markdown(kpi("Phiên truy cập", f"{n_sessions:,}", NAVY), unsafe_allow_html=True)
k[1].markdown(kpi("Tỉ lệ chuyển đổi", f"{conv_rate:.1%}", BLUE), unsafe_allow_html=True)
k[2].markdown(kpi("Doanh thu mô phỏng", f"${revenue:,.0f}", GREEN), unsafe_allow_html=True)
k[3].markdown(kpi(f"Giá đề xuất (giảm {decision['optimal_discount']})", decision["recommended_price"], ORANGE), unsafe_allow_html=True)
k[4].markdown(kpi("Lợi ích / 100 lượt xem", f"{rev_lift:+,.0f} $", RED if rev_lift < 0 else GREEN), unsafe_allow_html=True)

st.write("")
# ---------------- GAUGES ----------------
avg_comp = sum(prod["competitors"].values()) / len(prod["competitors"])
g = st.columns(4)
g[0].plotly_chart(gauge("Chuyển đổi dự đoán (%)", opt_conv * 100, 0, 100, ".1f",
                        [{"range": [0, 30], "color": "#f6d5d5"}, {"range": [30, 55], "color": "#fbe9c6"}, {"range": [55, 100], "color": "#d4eedf"}]),
                  width="stretch")
g[1].plotly_chart(gauge("AI Sentiment", sentiment, 0, 1, ".2f",
                        [{"range": [0, .45], "color": "#f6d5d5"}, {"range": [.45, .7], "color": "#fbe9c6"}, {"range": [.7, 1], "color": "#d4eedf"}], GREEN),
                  width="stretch")
g[2].plotly_chart(gauge("Giá đề xuất / đối thủ TB", opt_price / avg_comp, 0.6, 1.4, ".2f",
                        [{"range": [0.6, 0.95], "color": "#d4eedf"}, {"range": [0.95, 1.05], "color": "#fbe9c6"}, {"range": [1.05, 1.4], "color": "#f6d5d5"}], ORANGE),
                  width="stretch")
g[3].plotly_chart(gauge("Mức độ đau (pain severity)", severity, 0, 1, ".2f",
                        [{"range": [0, .3], "color": "#d4eedf"}, {"range": [.3, .6], "color": "#fbe9c6"}, {"range": [.6, 1], "color": "#f6d5d5"}], RED),
                  width="stretch")

st.markdown(f"<div class='agent'><b>🤖 Pricing Agent — {decision['strategy_name']}:</b> {decision['executive_summary']}</div>", unsafe_allow_html=True)
st.write("")

# ---------------- ROW: CURVE + COMPETITORS ----------------
c1, c2 = st.columns([1.5, 1])
with c1:
    st.markdown("<div class='ptitle'>Đường cong độ nhạy giá &amp; doanh thu kỳ vọng</div>", unsafe_allow_html=True)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=sim["offered_price"], y=sim["expected_revenue_per_100_visits"], name="Doanh thu / 100 lượt ($)",
                         marker_color=[ORANGE if p == opt_price else "#b9d3ea" for p in sim["offered_price"]]), secondary_y=False)
    fig.add_trace(go.Scatter(x=sim["offered_price"], y=sim["conversion_prob"] * 100, name="Chuyển đổi (%)",
                             mode="lines+markers", line=dict(color=NAVY, width=3)), secondary_y=True)
    fig.add_vline(x=comp_price, line_dash="dash", line_color=RED, annotation_text=f"{comp_name} ${comp_price:.0f}")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=80, b=10), legend=dict(orientation="h", yanchor="bottom", y=1.02),
                      plot_bgcolor="#fff", bargap=0.15)
    fig.update_yaxes(title_text="Doanh thu ($)", secondary_y=False)
    fig.update_yaxes(title_text="Chuyển đổi (%)", secondary_y=True)
    st.plotly_chart(fig, width="stretch")
with c2:
    st.markdown("<div class='ptitle'>So sánh giá với đối thủ</div>", unsafe_allow_html=True)
    ct = analytics.competitor_table(prod["product_id"], opt_price)
    fig = px.bar(ct, x="price", y="seller", orientation="h", text=ct["price"].map(lambda v: f"${v:,.2f}"),
                 color=ct["seller"].eq("Giá đề xuất (ta)"), color_discrete_map={True: ORANGE, False: BLUE})
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10), showlegend=False, plot_bgcolor="#fff",
                      yaxis_title=None, xaxis_title="Giá ($)")
    st.plotly_chart(fig, width="stretch")

# ---------------- ROW: MAP + REGION BARS ----------------
m1, m2 = st.columns([1, 1.2])
reg_df = analytics.region_summary(analytics.filter_sessions(df, category=category, product_id=prod["product_id"]))
with m1:
    st.markdown("<div class='ptitle'>Bản đồ doanh thu &amp; chuyển đổi theo vùng</div>", unsafe_allow_html=True)
    fig = px.scatter_geo(reg_df, lat="lat", lon="lon", size="revenue", color="conversion_rate", hover_name="region",
                         hover_data={"lat": False, "lon": False, "revenue": ":,.0f", "conversion_rate": ":.1%", "avg_sentiment": ":.2f"},
                         color_continuous_scale="RdYlGn", size_max=38)
    fig.update_geos(scope="asia", fitbounds="locations", showcountries=True, countrycolor="#c9d1dc",
                    showland=True, landcolor="#f4f6f9", showocean=True, oceancolor="#e8f1fa", resolution=50)
    fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), coloraxis_colorbar=dict(title="Conv."))
    st.plotly_chart(fig, width="stretch")
with m2:
    st.markdown("<div class='ptitle'>Chuyển đổi &amp; sentiment theo khu vực</div>", unsafe_allow_html=True)
    rs = reg_df.sort_values("revenue")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=rs["region"], y=rs["revenue"], name="Doanh thu ($)", marker_color=BLUE), secondary_y=False)
    fig.add_trace(go.Scatter(x=rs["region"], y=rs["conversion_rate"] * 100, name="Chuyển đổi (%)", mode="lines+markers",
                             line=dict(color=ORANGE, width=3)), secondary_y=True)
    fig.add_trace(go.Scatter(x=rs["region"], y=rs["avg_sentiment"] * 100, name="Sentiment ×100", mode="lines+markers",
                             line=dict(color=GREEN, width=2, dash="dot")), secondary_y=True)
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=80, b=10), legend=dict(orientation="h", yanchor="bottom", y=1.02), plot_bgcolor="#fff")
    st.plotly_chart(fig, width="stretch")

# ---------------- ROW: CATEGORY + COMPLAINTS + RAG ----------------
r1, r2, r3 = st.columns([1.1, 0.9, 1.1])
all_cat = analytics.category_summary(df if region == "Tất cả" else analytics.filter_sessions(df, region=region))
gap = analytics.category_competitor_gap(df)
with r1:
    st.markdown("<div class='ptitle'>So sánh ngành hàng: giá ta vs đối thủ chính (%)</div>", unsafe_allow_html=True)
    fig = px.bar(gap, x="category", y="gap_pct", text=gap["gap_pct"].map(lambda v: f"{v:+.1f}%"),
                 color="gap_pct", color_continuous_scale="RdYlGn_r")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), coloraxis_showscale=False, plot_bgcolor="#fff",
                      xaxis_title=None, yaxis_title="Chênh lệch giá (%)")
    st.plotly_chart(fig, width="stretch")
with r2:
    st.markdown("<div class='ptitle'>Cơ cấu phàn nàn của khách</div>", unsafe_allow_html=True)
    cb = analytics.complaint_breakdown(scope)
    fig = px.pie(cb, names="complaint_category", values="sessions", hole=0.45,
                 color_discrete_sequence=[GREEN, ORANGE, RED, BLUE])
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.1))
    st.plotly_chart(fig, width="stretch")
with r3:
    st.markdown("<div class='ptitle'>🔍 Voice-of-Customer (RAG)</div>", unsafe_allow_html=True)
    st.caption(f"Friction: `{', '.join(evidence['top_complaints']) or 'None'}`")
    for item in evidence["evidence_quotes"]:
        with st.container(border=True):
            st.markdown(f"*\"{item['review_text']}\"*")
            st.caption(f"Tag: **{item['complaint_category']}** · Sentiment `{item['sentiment_score']:.2f}`")

st.markdown("<div class='ptitle'>Bảng tổng hợp theo ngành hàng</div>", unsafe_allow_html=True)
show = all_cat.rename(columns={"category": "Ngành hàng", "sessions": "Phiên", "conversion_rate": "Chuyển đổi",
                               "avg_sentiment": "Sentiment TB", "avg_severity": "Pain TB", "revenue": "Doanh thu ($)"})
st.dataframe(show.style.format({"Chuyển đổi": "{:.1%}", "Sentiment TB": "{:.2f}", "Pain TB": "{:.2f}", "Doanh thu ($)": "{:,.0f}"}),
             width="stretch", hide_index=True)
