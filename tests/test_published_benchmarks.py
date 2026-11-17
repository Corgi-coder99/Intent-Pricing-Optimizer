from pathlib import Path

import pandas as pd
import pytest

from src.pipeline.nodes import market_margin

BENCH = Path(__file__).resolve().parent.parent / "src" / "pipeline" / "reference" / "published_benchmarks.csv"
VAT = pd.DataFrame([{"country": "FR", "reduced_rate_food": 0.0}, {"country": "DE", "reduced_rate_food": 0.0}])


def comp(country, price, code="1"):
    return {"product_code": code, "country": country, "currency": "EUR", "n_obs": 5, "n_stores": 3,
            "median_price": price, "confidence": "ok"}


def test_benchmark_file_is_internally_consistent_and_sourced():
    b = pd.read_csv(BENCH, comment="#")
    v = b.set_index("metric")["value"]
    assert (v["revenue"] - v["cost_of_sales"]) == pytest.approx(v["gross_profit"], abs=0.05)
    assert v["gross_margin_pct"] == pytest.approx(v["gross_profit"] / v["revenue"] * 100, abs=0.01)
    assert v["contribution_after_fulfilment_marketing_pct"] == pytest.approx(
        (v["gross_profit"] - v["fulfilment_costs"] - v["marketing_costs"]) / v["revenue"] * 100, abs=0.01)
    assert b["source_url"].str.startswith("https://corporate.zalando.com/").all()
    assert set(b[b["derived"] == "yes"]["metric"]) == {"gross_margin_pct", "fulfilment_cost_pct", "marketing_cost_pct",
                                                        "contribution_after_fulfilment_marketing_pct"}


def test_scenario_anchors_cost_at_home_and_shows_margin_elsewhere():
    df = pd.DataFrame([comp("FR", 10.0), comp("DE", 12.0)])
    out = market_margin(df, None, VAT, None, home="FR", benchmark_gross_margin_pct=40.0).set_index("country")
    assert out.loc["FR", "scenario_unit_cost_eur"] == pytest.approx(6.0)
    assert out.loc["FR", "scenario_margin_pct"] == pytest.approx(40.0)                 # nước nhà = đúng biên công bố
    assert out.loc["DE", "scenario_margin_pct"] == pytest.approx((12 - 6) / 12 * 100)   # 50%: giá cao hơn -> biên cao hơn


def test_no_scenario_without_benchmark_or_home_price():
    df = pd.DataFrame([comp("FR", 10.0)])
    assert market_margin(df, None, VAT, None)["scenario_margin_pct"].isna().all()
    only_de = pd.DataFrame([comp("DE", 12.0)])
    assert market_margin(only_de, None, VAT, None, benchmark_gross_margin_pct=40.0)["scenario_margin_pct"].isna().all()


def test_sector_margin_column_and_eurostat_anchor_are_used_when_available():
    df = pd.DataFrame([comp("FR", 10.0), comp("DE", 12.0)])
    out = market_margin(df, None, VAT, None, home="FR", benchmark_gross_margin_pct=21.02,
                        sector_gross_margin_pct={"FR": 21.02, "DE": 23.41}).set_index("country")
    assert out.loc["DE", "sector_gross_margin_pct"] == 23.41
    assert out.loc["FR", "scenario_margin_pct"] == pytest.approx(21.02)


def test_eurostat_reference_file_matches_its_own_arithmetic():
    ref = Path(__file__).resolve().parent.parent / "src" / "pipeline" / "reference" / "eurostat_retail_margin.csv"
    t = pd.read_csv(ref, comment="#")
    assert (t["gross_margin_pct"] - t["gross_margin_m_eur"] / t["turnover_m_eur"] * 100).abs().max() < 0.01
    assert (t["year"] <= 2019).all() and set(t["nace_r2"]) == {"G47", "G4711"}
    assert {"FR", "DE", "GB", "IE", "ES", "IT", "NL", "BE"} <= set(t["country"])
