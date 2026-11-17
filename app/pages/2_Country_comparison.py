import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import market_view
from src.agent_decision import money, number
from src.i18n import streamlit_lang, t

st.set_page_config(page_title=t("cc_page_title", st.session_state.get("lang", "en")), page_icon="🌍", layout="wide")
lang = streamlit_lang()

NAVY, BLUE, ORANGE, RED, GREEN = "#1f3a5f", "#2f7fc1", "#f39c12", "#d64545", "#2e9e6b"
st.markdown(
    f"""
<style>
.block-container {{padding-top: 3.2rem; padding-bottom: 1rem; max-width: 1500px;}}
.kpi {{border-radius: 8px; padding: 14px 10px; overflow: hidden; color: #fff; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.18);}}
.kpi .v {{font-size: clamp(1.05rem, 1.7vw, 1.9rem); white-space: nowrap; font-weight: 700; line-height: 1.1;}}
.kpi .l {{font-size: clamp(.64rem, .9vw, .82rem); opacity: .92; margin-top: 2px; overflow-wrap: normal; word-break: normal;}}
.ptitle {{font-weight:600; color:{NAVY}; font-size:.95rem; margin:6px 0 0 4px;}}
</style>
""",
    unsafe_allow_html=True,
)


def show(fig):
    if lang == "fr":
        fig.update_layout(separators=", ")
    st.plotly_chart(fig, width="stretch")


def kpi(label, value, color):
    return f'<div class="kpi" style="background:{color}"><div class="v">{value}</div><div class="l">{label}</div></div>'


st.markdown(f"<h3 style='color:{NAVY};margin:0'>🌍 {t('cc_title', lang)}</h3>", unsafe_allow_html=True)
st.caption(t("cc_caption", lang))

data = market_view.load_market()
if data is None:
    st.warning(t("cc_no_data", lang))
    st.code("python -m src.connectors.openprices\npython -m src.connectors.ecb_fx\npython -m src.pipeline run", language="bash")
    st.stop()

market, names, coords, gbp = data["market"], data["names"], data["coords"], data["gbp"]
home = str(market["home_country"].iloc[0]) if len(market) else "FR"

f1, f2 = st.columns([1.5, 2])
only_ok = f1.checkbox(t("cc_reliable", lang), value=False, help=t("cc_reliable_help", lang), key="cc_only_ok")
all_countries = sorted(c for c in market["country"].unique() if c != home)
picked = f2.multiselect(t("cc_countries", lang, home=home), all_countries, default=all_countries, key="cc_picked")

summary = market_view.country_summary(market, only_ok, picked)
codes = market_view.comparable_products(market, only_ok, picked)
scope = market[market["country"].isin(set(picked) | {home})]                     # KPIs follow the country selection
ok_share = (scope["confidence"] == "ok").mean() * 100 if len(scope) else 0       # data-quality indicator (independent of the checkbox)
shown_rows = scope[scope["confidence"] == "ok"] if only_ok else scope             # "Countries with data" follows the reliability filter
n_compared = market_view.compared_product_count(market, only_ok, picked)

k = st.columns(4)
k[0].markdown(kpi(t("cc_kpi_products", lang, home=home), f"{n_compared:,}", NAVY), unsafe_allow_html=True)
k[1].markdown(kpi(t("cc_kpi_countries", lang), f"{shown_rows['country'].nunique()}", BLUE), unsafe_allow_html=True)
k[2].markdown(kpi(t("cc_kpi_reliable", lang), f"{ok_share:.0f}" + (" %" if lang == "fr" else "%"),
                  ORANGE if ok_share < 50 else GREEN), unsafe_allow_html=True)
if gbp:
    rate = f"{gbp['eur_per_unit']:.4f}".replace(".", ",") if lang == "fr" else f"{gbp['eur_per_unit']:.4f}"
    k[3].markdown(kpi(t("cc_kpi_fx", lang, date=gbp["rate_date"]), rate, GREEN), unsafe_allow_html=True)
else:
    k[3].markdown(kpi(t("cc_kpi_fx_none", lang), t("cc_none", lang), RED), unsafe_allow_html=True)
if gbp and gbp.get("age_days", 0) > 7:
    st.warning(t("cc_fx_stale", lang, days=gbp["age_days"]))
st.write("")

if summary.empty:
    st.info(t("cc_empty", lang))
else:
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown(f"<div class='ptitle'>{t('cc_gap_title', lang, home=home)}</div>", unsafe_allow_html=True)
        s = summary.sort_values("median_vs_home_pct")
        fig = px.bar(s, x="country", y="median_vs_home_pct", text=s["n_products"].map(lambda n: f"n={n}"),
                     color="median_vs_home_pct", color_continuous_scale="RdYlGn_r", color_continuous_midpoint=0)
        fig.update_traces(customdata=s[["n_products"]].to_numpy(),
                          hovertemplate="<b>%{x}</b><br>" + t("cc_median_gap", lang) + ": %{y:+.1f}<br>"
                          + t("cc_n_products", lang) + ": %{customdata[0]}<extra></extra>")
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), coloraxis_showscale=False, plot_bgcolor="#fff",
                          xaxis_title=None, yaxis_title=t("cc_gap_axis", lang, home=home))
        show(fig)
    with right:
        st.markdown(f"<div class='ptitle'>{t('cc_margin_title', lang)}</div>", unsafe_allow_html=True)
        fig = go.Figure()
        fig.add_bar(x=summary["country"], y=summary["median_scenario_margin_pct"], name=t("cc_scenario", lang), marker_color=BLUE,
                    hovertemplate="%{x}<br>" + t("cc_scenario", lang) + ": %{y:.1f}<extra></extra>")
        fig.add_bar(x=summary["country"], y=summary["sector_gross_margin_pct"], name=t("cc_sector", lang), marker_color=ORANGE,
                    hovertemplate="%{x}<br>" + t("cc_sector", lang) + ": %{y:.1f}<extra></extra>")
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=80, b=10), barmode="group", plot_bgcolor="#fff",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02), yaxis_title=t("cc_pct_revenue", lang))
        show(fig)

    if len(coords):
        st.markdown(f"<div class='ptitle'>{t('cc_map_title', lang, home=home)}</div>", unsafe_allow_html=True)
        mp = summary.merge(coords, on="country", how="inner")
        if len(mp):
            fig = px.scatter_geo(mp, lat="lat", lon="lon", size=mp["n_products"].clip(lower=1) * 6, color="median_vs_home_pct",
                                 hover_name="country", color_continuous_scale="RdYlGn_r", color_continuous_midpoint=0,
                                 hover_data={"lat": False, "lon": False, "n_products": True, "median_vs_home_pct": ":.1f"},
                                 labels={"country": t("cc_country", lang), "n_products": t("cc_n_products", lang),
                                         "median_vs_home_pct": t("cc_median_gap", lang)})
            fig.update_traces(customdata=mp[["country", "n_products", "median_vs_home_pct"]].to_numpy(),
                              hovertemplate="<b>%{customdata[0]}</b><br>" + t("cc_n_products", lang)
                              + ": %{customdata[1]}<br>" + t("cc_median_gap", lang) + ": %{customdata[2]:+.1f}<extra></extra>")
            fig.update_geos(scope="europe", showcountries=True, countrycolor="#c9d1dc", showland=True, landcolor="#f4f6f9",
                            showocean=True, oceancolor="#e8f1fa", lataxis_range=[34, 60], lonaxis_range=[-12, 20])
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), coloraxis_colorbar=dict(title="% vs " + home))
            show(fig)

st.markdown(f"<div class='ptitle'>{t('cc_detail', lang)}</div>", unsafe_allow_html=True)
if not codes:
    st.info(t("cc_no_products", lang))
else:
    label = lambda c: f"{names.get(c, t('cc_generic_product', lang))} ({c})"
    code = st.selectbox(t("cc_product", lang), codes, format_func=label)
    tbl = market_view.product_table(market, code)
    tbl["reliability"] = tbl["confidence"].map({"ok": t("cc_conf_ok", lang), "low": t("cc_conf_low", lang)})
    fig = px.bar(tbl, x="country", y="market_net_eur", color="reliability", labels={"reliability": t("cc_col_conf", lang)}, text=tbl["market_net_eur"].map(lambda v: money(v, lang)),
                 color_discrete_map={t("cc_conf_ok", lang): GREEN, t("cc_conf_low", lang): ORANGE})
    fig.update_traces(hovertemplate="%{x}<br>" + t("cc_net_price", lang) + ": %{y:,.2f}<extra></extra>")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="#fff", xaxis_title=None,
                      yaxis_title=t("cc_net_price", lang))
    show(fig)
    num = lambda v, d=2: "—" if v != v or v is None else number(float(v), lang, d)      # localized, NaN -> dash
    shown = tbl.assign(market_net_eur=tbl["market_net_eur"].map(lambda v: money(v, lang)),
                       vs_home_pct=tbl["vs_home_pct"].map(lambda v: num(v, 1)),
                       scenario_margin_pct=tbl["scenario_margin_pct"].map(lambda v: num(v, 1)),
                       sector_gross_margin_pct=tbl["sector_gross_margin_pct"].map(lambda v: num(v, 1)))
    st.dataframe(shown.drop(columns=["confidence"]).rename(columns={
        "country": t("cc_col_country", lang), "market_net_eur": t("cc_net_price", lang), "vs_home_pct": f"% vs {home}",
        "scenario_margin_pct": t("cc_col_scenario", lang), "sector_gross_margin_pct": t("cc_col_sector", lang),
        "n_obs": t("cc_col_obs", lang), "n_stores": t("cc_col_stores", lang), "reliability": t("cc_col_conf", lang)}),
        width="stretch", hide_index=True)

with st.expander(t("cc_howto", lang), expanded=True):
    st.markdown("\n".join(f"- {t(k, lang)}" for k in ("cc_note_1", "cc_note_2", "cc_note_3", "cc_note_4", "cc_note_5")))
