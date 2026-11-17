import numpy as np
import pandas as pd
import pytest

from src import real_elasticity as re_


def synthetic_panel(true_eps=-1.5, n_products=60, n_weeks=45, seed=0, price_noise=0.2):
    rng = np.random.default_rng(seed)
    rows = []
    weeks = pd.date_range("2010-01-04", periods=n_weeks, freq="W-MON")
    season = rng.normal(0, 0.3, n_weeks)                       # common demand shock per week
    for i in range(n_products):
        base_p, base_q = rng.uniform(1, 20), rng.uniform(50, 500)
        for t, w in enumerate(weeks):
            p = base_p * np.exp(rng.normal(0, price_noise))
            q = base_q * (p / base_p) ** true_eps * np.exp(season[t] + rng.normal(0, 0.15))
            rows.append({"product": f"P{i}", "week": w, "units": q, "price": p, "invoices": 5})
    return pd.DataFrame(rows)


def test_recovers_known_elasticity_with_ci_covering_truth():
    out = re_.estimate_elasticity(re_.quality_gate(synthetic_panel(true_eps=-1.5)), n_boot=200)
    assert out["elasticity"] == pytest.approx(-1.5, abs=0.1)
    assert out["ci95"][0] <= -1.5 <= out["ci95"][1] and out["share_negative"] > 0.95


def test_recovers_inelastic_demand_too():
    out = re_.estimate_elasticity(re_.quality_gate(synthetic_panel(true_eps=-0.4, seed=3)), n_boot=100)
    assert out["elasticity"] == pytest.approx(-0.4, abs=0.1)


def test_week_fixed_effects_remove_common_demand_shocks():
    # price and demand rising together with the season (common shock) would bias the result without week effects
    rng = np.random.default_rng(1)
    df = synthetic_panel(true_eps=-1.0, seed=1)
    season = df.groupby("week").ngroup().map(dict(enumerate(rng.normal(0, 0.5, df["week"].nunique()))))
    df["price"] *= np.exp(0.5 * season)
    df["units"] *= np.exp(season)
    out = re_.estimate_elasticity(re_.quality_gate(df), n_boot=50)
    assert out["elasticity"] == pytest.approx(-1.0, abs=0.15)


def test_quality_gate_drops_thin_flat_priced_and_noisy_products():
    good = synthetic_panel(n_products=3)
    flat = good[good["product"] == "P0"].assign(product="FLAT", price=2.0)               # constant price
    thin = good[good["product"] == "P1"].head(10).assign(product="THIN")                 # few weeks
    one_inv = good[good["product"] == "P2"].assign(product="ONEINV", invoices=1)         # one invoice per week
    out = re_.quality_gate(pd.concat([good, flat, thin, one_inv]))
    assert set(out["product"]) == {"P0", "P1", "P2"}


def test_extreme_price_typos_are_removed():
    df = synthetic_panel(n_products=2)
    df.loc[df.index[0], "price"] *= 1000
    out = re_.quality_gate(df)
    assert out["price"].max() < 5 * df.groupby("product")["price"].median().max()


def test_empty_after_gate_raises():
    with pytest.raises(ValueError):
        re_.estimate_elasticity(pd.DataFrame(columns=["product", "week", "units", "price"]))


def test_load_weekly_reads_duckdb_read_only_and_filters_line_types(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create table clean_lines(invoice varchar, stock_code varchar, quantity bigint, invoice_date date, price double,"
                " country varchar, line_type varchar)")
    con.execute("insert into clean_lines values ('1','A',10,'2010-01-04',2.0,'United Kingdom','sale'),"
                "('2','A',5,'2010-01-05',4.0,'United Kingdom','sale'),('3','A',-5,'2010-01-05',4.0,'United Kingdom','cancellation'),"
                "('4','A',3,'2010-01-05',2.0,'France','sale'),('5','A',3,'2010-01-05',0,'France','sale')")
    con.close()
    uk = re_.load_weekly(db, "uk")
    assert len(uk) == 1 and uk["units"].iloc[0] == 15 and uk["price"].iloc[0] == pytest.approx((10 * 2 + 5 * 4) / 15)
    assert re_.load_weekly(db, "eu")["units"].iloc[0] == 3
    with pytest.raises(Exception):
        duckdb.connect(str(db), read_only=True).execute("insert into clean_lines values ('x','A',1,'2010-01-04',1,'x','sale')")
