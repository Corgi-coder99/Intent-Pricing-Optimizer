import io
import json
import urllib.error

import pandas as pd
import pytest

from src.connectors import openprices as op
from src.pipeline import Layout, Policy, run_pipeline


def item(**over):
    base = {
        "id": 1, "product_code": "3017620422003", "product_name": "Nutella", "price": 3.5, "currency": "EUR",
        "date": "2026-01-10", "duplicate_of": None, "owner": "secret-user", "owner_comment": "my note",
        "proof": {"file_path": "x.jpg"}, "proof_id": 9,
        "product": {"product_name": "Nutella", "image_url": "https://evil.example/x.png",
                    "categories_tags": ["en:spreads", "en:chocolate-spreads"]},
        "location": {"osm_name": "Carrefour", "osm_address_city": "Lyon", "osm_address_country_code": "FR",
                     "osm_lat": 45.7, "osm_lon": 4.8},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- URL / network
@pytest.mark.parametrize("url", [
    "http://prices.openfoodfacts.org/api/v1/prices",                      # not HTTPS
    "https://evil.com/api/v1/prices",
    "https://prices.openfoodfacts.org.evil.com/api/v1/prices",            # fake subdomain
    "https://prices.openfoodfacts.org@evil.com/api/v1/prices",            # userinfo trick
    "https://user:pw@prices.openfoodfacts.org/api/v1/prices",
    "https://prices.openfoodfacts.org:8443/api/v1/prices",
    "https://prices.openfoodfacts.org/other",                             # wrong path
    "file:///etc/passwd",
])
def test_url_allowlist_blocks_bad_urls(url):
    with pytest.raises(op.ConnectorError):
        op.validate_url(url)


def test_build_url_validates_parameters():
    assert op.build_url("EUR", 1).startswith("https://prices.openfoodfacts.org/api/v1/prices?")
    for bad in (lambda: op.build_url("USD", 1), lambda: op.build_url("EUR", 0), lambda: op.build_url("EUR", 1, 1000),
                lambda: op.build_url("EUR;rm -rf", 1)):
        with pytest.raises(ValueError):
            bad()


def test_redirects_are_refused():
    with pytest.raises(op.ConnectorError):
        op._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.com")


def test_oversized_response_is_cut_off():
    with pytest.raises(op.ResponseTooLarge):
        op.read_capped(io.BytesIO(b"x" * 100), limit=50)
    assert op.read_capped(io.BytesIO(b"x" * 50), limit=50) == b"x" * 50


# ---------------------------------------------------------------- sanitising & whitelist
def test_only_whitelisted_fields_and_no_personal_data():
    row = op.parse_item(item())
    assert list(row) == op.OUTPUT_COLUMNS
    blob = json.dumps(row)
    for leaked in ("secret-user", "my note", "x.jpg", "evil.example"):
        assert leaked not in blob


@pytest.mark.parametrize("over", [
    {"price": -1}, {"price": 0}, {"price": float("nan")}, {"price": float("inf")}, {"price": "3.5"}, {"price": True},
    {"price": 10 ** 9}, {"product_code": "../../etc/passwd"}, {"product_code": "12"}, {"currency": "USD"},
    {"date": "yesterday"}, {"duplicate_of": 5},
    {"location": {"osm_address_country_code": "US"}}, {"location": None},
])
def test_invalid_or_out_of_scope_items_dropped(over):
    assert op.parse_item(item(**over)) is None


def test_text_sanitised_against_formula_injection_and_control_chars():
    r = op.parse_item(item(product_name='=HYPERLINK("http://evil","x")'))
    assert r["product_name"].startswith("'=")
    r = op.parse_item(item(product_name="Milk\x00\x07‮ evil | pipe\n\ttext"))
    assert "\x00" not in r["product_name"] and "‮" not in r["product_name"]
    assert "|" not in r["product_name"] and "\n" not in r["product_name"]
    assert len(op.parse_item(item(product_name="a" * 5000))["product_name"]) <= 201


def test_geo_out_of_range_becomes_null_not_error():
    r = op.parse_item(item(location={"osm_address_country_code": "FR", "osm_lat": 999, "osm_lon": 4.8}))
    assert r["lat"] is None and r["lon"] == 4.8


# ---------------------------------------------------------------- fetching, limits, retries
def make_fetch(pages, calls):
    def fetch(url):
        calls.append(url)
        n = len(calls) - 1
        return json.dumps({"items": pages[n] if n < len(pages) else []}).encode()
    return fetch


def test_stops_at_max_records_and_max_pages():
    calls = []
    df = op.fetch_prices(["EUR"], ["FR"], max_records=3, max_pages=10,
                         fetch=make_fetch([[item(id=i) for i in range(2)]] * 10, calls), sleep=lambda s: None)
    assert len(df) == 3 and len(calls) == 2
    calls = []
    op.fetch_prices(["EUR"], ["FR"], max_records=10_000, max_pages=4,
                    fetch=make_fetch([[item()]] * 10, calls), sleep=lambda s: None)
    assert len(calls) == 4


def test_out_of_scope_countries_are_filtered_client_side():
    calls = []
    page = [item(), item(location={"osm_address_country_code": "ES", "osm_name": "X"})]
    df = op.fetch_prices(["EUR"], ["FR", "GB"], max_pages=1, fetch=make_fetch([page], calls), sleep=lambda s: None)
    assert set(df["country"]) == {"FR"}


def test_retries_on_429_then_succeeds_and_gives_up_eventually():
    sleeps, state = [], {"n": 0}

    def flaky(url):
        state["n"] += 1
        if state["n"] < 3:
            raise urllib.error.HTTPError(url, 429, "slow down", {"Retry-After": "2"}, None)
        return json.dumps({"items": []}).encode()

    op.fetch_prices(["EUR"], max_pages=1, fetch=flaky, sleep=sleeps.append)
    assert state["n"] == 3 and sleeps

    def always(url):
        raise urllib.error.HTTPError(url, 503, "down", {}, None)

    with pytest.raises(op.ConnectorError):
        op.fetch_prices(["EUR"], max_pages=1, fetch=always, sleep=lambda s: None)


def test_404_after_last_page_means_end_of_data_not_error():
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            return json.dumps({"items": [item()]}).encode()
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    df = op.fetch_prices(["EUR"], ["FR"], max_pages=5, fetch=fetch, sleep=lambda s: None)
    assert len(df) == 1 and len(calls) == 2


def test_client_errors_and_bad_payloads_are_not_retried():
    n = {"c": 0}

    def not_found(url):
        n["c"] += 1
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    with pytest.raises(op.ConnectorError):          # a 404 on page 1 is still a real error
        op.fetch_prices(["EUR"], max_pages=1, fetch=not_found, sleep=lambda s: None)
    assert n["c"] == 1
    for payload in (b"not json", b'["list"]', b'{"items": "nope"}'):
        with pytest.raises(op.ConnectorError):
            op.fetch_prices(["EUR"], max_pages=1, fetch=lambda u, p=payload: p, sleep=lambda s: None)


# ---------------------------------------------------------------- pipeline integration
def test_landing_file_flows_through_pipeline_and_is_idempotent(tmp_path):
    layout = Layout(tmp_path).ensure()
    rows = [op.parse_item(item(id=i, price=1 + i, location={"osm_name": f"S{i}", "osm_address_country_code": "FR",
                                                              "osm_address_city": "Lyon"})) for i in range(5)]
    rows.append(op.parse_item(item(product_name="=cmd|calc!A0", price=9)))
    df = pd.DataFrame(rows, columns=op.OUTPUT_COLUMNS)
    f1 = op.write_to_landing(df, layout.landing)
    assert op.write_to_landing(df, layout.landing) == f1                   # same content -> same file
    r = run_pipeline(layout, isolate=False, policy=Policy())
    assert r["ok"], r
    assert r["results"]["silver_price_observation"]["rows_total"] == 6
    s = pd.read_parquet(layout.silver / "price_observation.parquet")
    assert not s["product_name"].dropna().str.startswith("=").any()
    assert (layout.gold / "competitor_prices.parquet").exists()
    assert "owner" not in " ".join(s.columns)
    r2 = run_pipeline(layout, isolate=False, policy=Policy())
    assert r2["results"]["silver_price_observation"]["batches"] == 0
