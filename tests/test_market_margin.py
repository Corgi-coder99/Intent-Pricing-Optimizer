import pandas as pd
import pytest

from src.pipeline import Layout, Policy, run_pipeline
from src.pipeline.nodes import market_margin


def obs(country, currency, store, price, code="3017620422003"):
    return {"product_code": code, "price": price, "currency": currency, "country": country, "store": store,
            "city": "X", "observed_at": "2026-01-10"}


def build(layout, price_rows, fx=None, costs=None):
    pd.DataFrame(price_rows).to_csv(layout.landing / "prices.csv", index=False)
    if fx is not None:
        pd.DataFrame(fx).to_csv(layout.landing / "fx.csv", index=False)
    if costs is not None:
        pd.DataFrame(costs).to_csv(layout.landing / "costs.csv", index=False)
    r = run_pipeline(layout, isolate=False, policy=Policy())
    assert r["ok"], r
    return r, pd.read_parquet(layout.gold / "market_margin.parquet").set_index("country")


@pytest.fixture()
def layout(tmp_path):
    return Layout(tmp_path).ensure()


FR_ROWS = [obs("FR", "EUR", f"S{i}", 2.20) for i in range(4)]
GB_ROWS = [obs("GB", "GBP", f"G{i}", 2.00) for i in range(4)]


def test_prices_converted_to_net_eur_and_compared_with_home_country(layout):
    fx = [{"rate_date": "2026-01-10", "currency": "GBP", "eur_per_unit": 1.20}]
    r, m = build(layout, FR_ROWS + GB_ROWS, fx=fx)
    assert m.loc["FR", "market_net_eur"] == pytest.approx(2.20 / 1.055)          # FR food VAT 5.5%
    assert m.loc["GB", "market_net_eur"] == pytest.approx(2.00 * 1.20)           # GB food VAT 0%
    assert m.loc["GB", "vs_home_pct"] == pytest.approx((2.4 / (2.20 / 1.055) - 1) * 100, abs=0.01)
    assert m.loc["FR", "vs_home_pct"] == 0
    assert r["results"]["gold_market_margin"]["products_in_2plus_countries"] == 1


def test_missing_fx_rate_is_flagged_not_guessed(layout):
    r, m = build(layout, FR_ROWS + GB_ROWS)                                         # no GBP exchange rate
    assert m.loc["GB", "status"] == "fx_missing" and pd.isna(m.loc["GB", "market_net_eur"])
    assert m.loc["FR", "status"] == "ok"
    assert pd.isna(m.loc["GB", "vs_home_pct"])


def test_margin_from_branch_cost_in_other_currency(layout):
    fx = [{"rate_date": "2026-01-10", "currency": "GBP", "eur_per_unit": 1.20}]
    costs = [{"product_code": "3017620422003", "country": "FR", "unit_cost": 1.50, "currency": "EUR", "own_price": 2.50},
             {"product_code": "3017620422003", "country": "GB", "unit_cost": 1.00, "currency": "GBP", "own_price": 2.40}]
    _, m = build(layout, FR_ROWS + GB_ROWS, fx=fx, costs=costs)
    net_fr = 2.20 / 1.055
    assert m.loc["FR", "margin_at_market_pct"] == pytest.approx((net_fr - 1.5) / net_fr * 100, abs=0.01)
    assert m.loc["GB", "unit_cost_eur"] == pytest.approx(1.20)
    assert m.loc["GB", "margin_at_market_pct"] == pytest.approx((2.4 - 1.2) / 2.4 * 100, abs=0.01)   # 50%
    own_fr = 2.50 / 1.055
    assert m.loc["FR", "own_margin_pct"] == pytest.approx((own_fr - 1.5) / own_fr * 100, abs=0.01)
    assert m.loc["FR", "own_vs_market_pct"] == pytest.approx((own_fr / net_fr - 1) * 100, abs=0.01)


def test_bad_cost_and_fx_rows_are_quarantined(layout):
    fx = [{"rate_date": "2026-01-10", "currency": "GBP", "eur_per_unit": -1},
          {"rate_date": "2026-01-10", "currency": "GBP", "eur_per_unit": 1.2}]
    costs = [{"product_code": "1", "country": "FR", "unit_cost": 0, "currency": "EUR"}]
    r, _ = build(layout, FR_ROWS + GB_ROWS, fx=fx, costs=costs)
    assert r["results"]["silver_fx_rate"]["rejected"] == 1 and r["results"]["silver_branch_cost"]["rejected"] == 1


def test_latest_fx_rate_wins_and_old_rate_kept_in_history(layout):
    fx = [{"rate_date": "2026-01-10", "currency": "GBP", "eur_per_unit": 1.10},
          {"rate_date": "2026-01-12", "currency": "GBP", "eur_per_unit": 1.30}]
    _, m = build(layout, GB_ROWS, fx=fx)
    assert m.loc["GB", "eur_per_unit"] == 1.30


def test_pure_function_handles_no_fx_no_cost():
    comp = pd.DataFrame([{"product_code": "1", "country": "FR", "currency": "EUR", "n_obs": 3, "n_stores": 2,
                          "median_price": 1.055, "confidence": "ok"}])
    vat = pd.DataFrame([{"country": "FR", "reduced_rate_food": 5.5}])
    out = market_margin(comp, None, vat, None)
    assert out.loc[0, "market_net_eur"] == pytest.approx(1.0) and pd.isna(out.loc[0, "margin_at_market_pct"])


def test_stale_fx_rate_is_ignored_not_used(layout):
    fx = [{"rate_date": "2025-12-31", "currency": "GBP", "eur_per_unit": 1.10},
          {"rate_date": "2026-11-17", "currency": "SEK", "eur_per_unit": 0.09}]
    _, m = build(layout, FR_ROWS + GB_ROWS, fx=fx)
    assert m.loc["GB", "status"] == "fx_missing" and pd.isna(m.loc["GB", "market_net_eur"])
