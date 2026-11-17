import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import market_view

st.set_page_config(page_title="So sánh quốc gia EU/UK", page_icon="🌍", layout="wide")

NAVY, BLUE, ORANGE, RED, GREEN = "#1f3a5f", "#2f7fc1", "#f39c12", "#d64545", "#2e9e6b"
st.markdown(
    f"""
<style>
.block-container {{padding-top: 3.2rem; padding-bottom: 1rem; max-width: 1500px;}}
.kpi {{border-radius: 8px; padding: 14px 16px; color: #fff; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.18);}}
.kpi .v {{font-size: 1.9rem; font-weight: 700; line-height: 1.1;}}
.kpi .l {{font-size: .82rem; opacity: .92; margin-top: 2px;}}
.ptitle {{font-weight:600; color:{NAVY}; font-size:.95rem; margin:6px 0 0 4px;}}
.warn {{border-left:5px solid {ORANGE}; background:#fff7e6; padding:10px 14px; border-radius:6px; color:#4a3b12; font-size:.9rem;}}
</style>
""",
    unsafe_allow_html=True,
)


def kpi(label, value, color):
    return f'<div class="kpi" style="background:{color}"><div class="v">{value}</div><div class="l">{label}</div></div>'


st.markdown(f"<h3 style='color:{NAVY};margin:0'>🌍 So sánh giá và biên lợi nhuận giữa các quốc gia EU/UK</h3>", unsafe_allow_html=True)
st.caption("Giá thị trường thật (Open Prices), đã trừ VAT và quy đổi sang EUR. Biên lợi nhuận là KỊCH BẢN GIẢ ĐỊNH, không phải lợi nhuận thật của công ty nào.")

data = market_view.load_market()
if data is None:
    st.warning("Chưa có bảng so sánh. Hãy chạy pipeline trong thư mục dự án:")
    st.code("python -m src.connectors.openprices\npython -m src.connectors.ecb_fx\npython -m src.pipeline run", language="bash")
    st.stop()

market, names, coords, gbp = data["market"], data["names"], data["coords"], data["gbp"]
home = str(market["home_country"].iloc[0]) if len(market) else "FR"

f1, f2 = st.columns([1, 2])
only_ok = f1.checkbox("Chỉ dữ liệu đủ tin cậy (≥2 cửa hàng, ≥3 quan sát)", value=False)
all_countries = sorted(c for c in market["country"].unique() if c != home)
picked = f2.multiselect(f"Quốc gia so sánh với {home}", all_countries, default=all_countries)

summary = market_view.country_summary(market, only_ok, picked)
codes = market_view.comparable_products(market, only_ok)
ok_share = (market["confidence"] == "ok").mean() * 100 if len(market) else 0

k = st.columns(4)
k[0].markdown(kpi("Sản phẩm so sánh được (≥2 nước)", f"{len(codes):,}", NAVY), unsafe_allow_html=True)
k[1].markdown(kpi("Quốc gia có dữ liệu", f"{market['country'].nunique()}", BLUE), unsafe_allow_html=True)
k[2].markdown(kpi("Dòng đủ tin cậy", f"{ok_share:.0f}%", ORANGE if ok_share < 50 else GREEN), unsafe_allow_html=True)
k[3].markdown(kpi(f"1 GBP = EUR (ECB {gbp['rate_date']})" if gbp else "Tỷ giá GBP", f"{gbp['eur_per_unit']:.4f}" if gbp else "chưa có",
                  GREEN if gbp else RED), unsafe_allow_html=True)
st.write("")

if summary.empty:
    st.info("Không có sản phẩm nào đủ điều kiện so sánh với bộ lọc hiện tại. Bỏ chọn 'Chỉ dữ liệu đủ tin cậy' hoặc chọn thêm quốc gia.")
else:
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown(f"<div class='ptitle'>Chênh lệch giá trung vị so với {home} (%) — số trên cột là số sản phẩm so sánh</div>", unsafe_allow_html=True)
        s = summary.sort_values("median_vs_home_pct")
        fig = px.bar(s, x="country", y="median_vs_home_pct", text=s["n_products"].map(lambda n: f"n={n}"),
                     color="median_vs_home_pct", color_continuous_scale="RdYlGn_r", color_continuous_midpoint=0)
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), coloraxis_showscale=False, plot_bgcolor="#fff",
                          xaxis_title=None, yaxis_title=f"% so với {home}")
        st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown("<div class='ptitle'>Biên giả định trung vị so với biên ngành thật của từng nước (Eurostat)</div>", unsafe_allow_html=True)
        fig = go.Figure()
        fig.add_bar(x=summary["country"], y=summary["median_scenario_margin_pct"], name="Biên giả định (neo ở nước nhà)", marker_color=BLUE)
        fig.add_bar(x=summary["country"], y=summary["sector_gross_margin_pct"], name="Biên ngành thực phẩm (Eurostat)", marker_color=ORANGE)
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), barmode="group", plot_bgcolor="#fff",
                          legend=dict(orientation="h", y=1.12), yaxis_title="% doanh thu")
        st.plotly_chart(fig, width="stretch")

    if len(coords):
        st.markdown(f"<div class='ptitle'>Bản đồ: chênh lệch giá so với {home}</div>", unsafe_allow_html=True)
        mp = summary.merge(coords, on="country", how="inner")
        if len(mp):
            fig = px.scatter_geo(mp, lat="lat", lon="lon", size=mp["n_products"].clip(lower=1) * 6, color="median_vs_home_pct",
                                 hover_name="country", color_continuous_scale="RdYlGn_r", color_continuous_midpoint=0,
                                 hover_data={"lat": False, "lon": False, "n_products": True, "median_vs_home_pct": ":.1f"})
            fig.update_geos(scope="europe", showcountries=True, countrycolor="#c9d1dc", showland=True, landcolor="#f4f6f9",
                            showocean=True, oceancolor="#e8f1fa", lataxis_range=[34, 60], lonaxis_range=[-12, 20])
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), coloraxis_colorbar=dict(title="% vs " + home))
            st.plotly_chart(fig, width="stretch")

st.markdown("<div class='ptitle'>Chi tiết từng sản phẩm</div>", unsafe_allow_html=True)
if not codes:
    st.info("Chưa có sản phẩm nào có giá ở từ 2 quốc gia trở lên.")
else:
    label = lambda c: f"{names.get(c, 'Sản phẩm')} ({c})"
    code = st.selectbox("Sản phẩm", codes, format_func=label)
    t = market_view.product_table(market, code)
    fig = px.bar(t, x="country", y="market_net_eur", color="confidence", text=t["market_net_eur"].map(lambda v: f"€{v:.2f}"),
                 color_discrete_map={"ok": GREEN, "low": ORANGE})
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="#fff", xaxis_title=None,
                      yaxis_title="Giá đã trừ VAT (EUR)")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(t.rename(columns={"country": "Nước", "market_net_eur": "Giá trừ VAT (EUR)", "vs_home_pct": f"% vs {home}",
                                   "scenario_margin_pct": "Biên giả định %", "sector_gross_margin_pct": "Biên ngành %",
                                   "n_obs": "Quan sát", "n_stores": "Cửa hàng", "confidence": "Tin cậy"}),
                 width="stretch", hide_index=True)

with st.expander("Cách đọc và giới hạn (đọc trước khi dùng)", expanded=True):
    st.markdown(
        "- **Biên giả định** giả sử mọi chi nhánh mua cùng một giá vốn, và giá vốn đó cho biên bằng biên ngành của nước nhà khi bán ở giá "
        "thị trường nước nhà. Nó cho biết biên *sẽ đổi thế nào* nếu bán ở giá nước khác, không phải lợi nhuận thật.\n"
        "- **Biên ngành (Eurostat)** là biên gộp của cả ngành siêu thị/thực phẩm mỗi nước, năm 2019 (Anh: 2018), không phải giá vốn của công ty nào.\n"
        "- **Dữ liệu giá** do cộng đồng đóng góp (Open Prices), thiên về thực phẩm, không đảm bảo độ đúng; ít sản phẩm có mặt ở nhiều nước.\n"
        "- **Bảng VAT chưa được kiểm chứng**; tỷ giá lấy từ ECB. Khi có giá vốn thật của chi nhánh, cột biên thật sẽ thay thế kịch bản.\n"
        "- Nguồn: Open Food Facts contributors (ODbL), ECB, Eurostat. Chi tiết ở `docs/DATA_SOURCES.md`."
    )
