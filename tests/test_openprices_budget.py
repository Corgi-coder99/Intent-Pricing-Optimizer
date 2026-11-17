import json
import urllib.parse

from src.connectors import openprices as op


def fetch_factory(n_locations, calls, valid_rows=False):
    """Mọi địa điểm đều trả dữ liệu KHÔNG hợp lệ (hoặc rất ít): kịch bản từng làm connector chạy hàng giờ."""
    def fetch(url):
        calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        page = int(q["page"][0])
        if u.path == op.LOCATIONS_PATH:
            items = [{"id": i + 1, "osm_address_country_code": "IE", "price_count": 5} for i in range(n_locations)] if page == 1 else []
            return json.dumps({"items": items}).encode()
        item = {"product_code": "bad-code", "price": 1.0, "currency": "EUR", "date": "2026-01-01",
                "location": {"osm_address_country_code": "IE"}}
        return json.dumps({"items": [item] if page == 1 else []}).encode()
    return fetch


def test_total_request_budget_stops_runaway_scan_and_keeps_partial_result():
    calls, logs = [], []
    df = op.fetch_prices_by_location(["IE"], per_country_max=1500, max_requests=15, max_locations_per_country=10_000,
                                     fetch=fetch_factory(5000, calls), sleep=lambda s: None, log=logs.append)
    assert len(calls) <= 15 and df.empty
    assert any("STOPPED EARLY" in m for m in logs)


def test_locations_per_country_cap_bounds_work_even_without_budget_exhaustion():
    calls = []
    op.fetch_prices_by_location(["IE"], per_country_max=1500, max_requests=10_000, max_locations_per_country=7,
                                fetch=fetch_factory(5000, calls), sleep=lambda s: None)
    price_calls = [c for c in calls if "location_id" in c]
    assert len({urllib.parse.parse_qs(urllib.parse.urlsplit(c).query)["location_id"][0] for c in price_calls}) <= 7


def test_budget_counter_blocks_further_requests():
    th = op._Throttle(lambda s: None, max_requests=2)
    th.wait()
    th.wait()
    try:
        th.wait()
    except op.RequestBudgetExceeded:
        return
    raise AssertionError("budget not enforced")
