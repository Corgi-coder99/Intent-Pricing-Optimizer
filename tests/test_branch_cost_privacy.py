import json
import subprocess
import sys

import pandas as pd
import pytest

from src.pipeline import Layout, Policy, run_pipeline

ROWS = [
    {"product_code": "3017620422003", "country": "fr", "unit_cost": 1.5, "currency": "eur", "own_price": 2.5,
     "product_name": "Spread", "manager_name": "Jean Dupont", "supplier_iban": "FR76 0000 0000"},
    {"product_code": "3017620422003", "country": "gb", "unit_cost": 1.0, "currency": "gbp", "own_price": 2.4,
     "product_name": "Spread", "manager_name": "Jane Roe", "supplier_iban": "GB00 0000"},
]


def test_columns_outside_the_contract_never_reach_silver_or_gold(tmp_path):
    layout = Layout(tmp_path).ensure()
    pd.DataFrame(ROWS).to_csv(layout.landing / "costs.csv", index=False)
    r = run_pipeline(layout, isolate=False, policy=Policy())
    assert r["ok"] and r["results"]["silver_branch_cost"]["rows_total"] == 2
    s = pd.read_parquet(layout.silver / "branch_cost.parquet")
    assert set(s.columns) <= {"product_code", "country", "unit_cost", "currency", "own_price", "product_name"}
    assert "Jean Dupont" not in s.to_csv() and "FR76" not in s.to_csv()
    assert list(s["currency"]) == ["EUR", "GBP"] and list(s["country"]) == ["FR", "GB"]


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "src.pipeline", *args], capture_output=True, text=True, encoding="utf-8")


def test_check_command_reports_without_ingesting_or_printing_values(tmp_path):
    bad = pd.DataFrame(ROWS)
    bad.loc[0, "unit_cost"] = -3
    f = tmp_path / "costs.csv"
    bad.to_csv(f, index=False)
    r = run_cli("check", "--file", str(f), "--type", "branch_cost")
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["rows"] == 2 and out["valid_rows"] == 1 and out["rejected"] == 1
    assert out["reject_reasons"] == {"cost_out_of_range": 1}
    assert sorted(out["columns_that_will_be_ignored"]) == ["manager_name", "supplier_iban"]
    assert "Jean Dupont" not in r.stdout and "FR76" not in r.stdout          # row contents are never printed


def test_check_flags_missing_required_columns_and_missing_file(tmp_path):
    f = tmp_path / "x.csv"
    pd.DataFrame({"product_code": ["1"], "country": ["FR"]}).to_csv(f, index=False)
    r = run_cli("check", "--file", str(f), "--type", "branch_cost")
    assert r.returncode == 1 and set(json.loads(r.stdout)["missing_required_columns"]) == {"unit_cost", "currency"}
    assert run_cli("check", "--file", str(tmp_path / "nope.csv"), "--type", "branch_cost").returncode == 2
