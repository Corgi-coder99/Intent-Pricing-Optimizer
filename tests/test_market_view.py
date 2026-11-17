import pandas as pd
import pytest

from src import market_view as mv
from src.pipeline import Layout, Policy, run_pipeline


def row(code, country, price, vs=None, scen=None, conf="ok", status="ok", sector=20.0):
    return {"product_code": code, "country": country, "market_net_eur": price, "vs_home_pct": vs, "scenario_margin_pct": scen,
            "sector_gross_margin_pct": sector, "n_obs": 5, "n_stores": 3, "confidence": conf, "status": status, "home_country": "FR"}


MARKET = pd.DataFrame([
    row("1", "FR", 10.0, 0.0, 21.0), row("1", "DE", 12.0, 20.0, 34.0), row("1", "IT", 9.0, -10.0, 12.0, conf="low"),
    row("2", "FR", 5.0, 0.0, 21.0), row("2", "DE", 6.0, 20.0, 34.0),
    row("3", "DE", 7.0, None, None), row("4", "GB", 3.0, None, None, status="fx_missing"),
])


def test_country_summary_excludes_home_unusable_rows_and_filters_by_confidence():
    s = mv.country_summary(MARKET).set_index("country")
    assert set(s.index) == {"DE", "IT"} and s.loc["DE", "n_products"] == 2 and s.loc["DE", "median_vs_home_pct"] == 20.0
    only = mv.country_summary(MARKET, only_confident=True)
    assert list(only["country"]) == ["DE"]
    assert list(mv.country_summary(MARKET, countries=["IT"])["country"]) == ["IT"]


def test_empty_selection_returns_empty_frame_with_columns():
    e = mv.country_summary(MARKET, countries=["ES"])
    assert e.empty and "median_vs_home_pct" in e.columns


def test_comparable_products_need_two_countries_with_valid_prices():
    assert mv.comparable_products(MARKET) == ["1", "2"]
    assert mv.comparable_products(MARKET, only_confident=True) == ["1", "2"]
    assert mv.comparable_products(MARKET[MARKET["country"] != "DE"], only_confident=True) == []


def test_product_table_sorted_and_only_ok_rows():
    t = mv.product_table(MARKET, "1")
    assert list(t["country"]) == ["DE", "FR", "IT"]
    assert mv.product_table(MARKET, "4").empty


def test_load_market_none_without_gold_and_reads_names_coords_fx(tmp_path):
    assert mv.load_market(tmp_path) is None
    layout = Layout(tmp_path).ensure()
    obs = [{"product_code": "3017620422003", "product_name": "Spread", "price": 2.2, "currency": "EUR", "country": "FR",
            "store": f"S{i}", "city": "X", "observed_at": "2026-01-10", "lat": 48.0 + i, "lon": 2.0} for i in range(4)]
    pd.DataFrame(obs).to_csv(layout.landing / "p.csv", index=False)
    pd.DataFrame([{"rate_date": "2026-01-10", "currency": "GBP", "eur_per_unit": 1.2}]).to_csv(layout.landing / "fx.csv", index=False)
    assert run_pipeline(layout, isolate=False, policy=Policy())["ok"]
    d = mv.load_market(tmp_path)
    assert d["names"]["3017620422003"] == "Spread" and d["gbp"] == {"eur_per_unit": 1.2, "rate_date": "2026-01-10"}
    assert list(d["coords"]["country"]) == ["FR"] and d["coords"]["lat"].iloc[0] == pytest.approx(49.5)


def test_products_with_home_data_and_more_observations_come_first():
    m = pd.DataFrame([
        row("A", "DE", 1.0), row("A", "IT", 1.0),                       # không có nước nhà
        row("B", "FR", 1.0), row("B", "DE", 1.0),                       # có nước nhà, 10 quan sát
        row("C", "FR", 1.0), row("C", "DE", 1.0), row("C", "IT", 1.0),  # có nước nhà, 15 quan sát
    ])
    assert mv.comparable_products(m) == ["C", "B", "A"]
