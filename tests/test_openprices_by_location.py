import json
import urllib.parse

import pytest

from src.connectors import openprices as op


def loc(i, country, count=10):
    return {"id": i, "osm_address_country_code": country, "price_count": count}


def price(i, country, store="S"):
    return {"id": i, "product_code": "3017620422003", "product_name": "X", "price": 1.0 + i / 100, "currency": "EUR",
            "date": "2026-01-10", "duplicate_of": None, "owner": "someone",
            "location": {"osm_name": store, "osm_address_country_code": country, "osm_address_city": "C"}}


def router(locations, prices_by_loc, calls):
    def fetch(url):
        calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        page = int(q["page"][0])
        if u.path == op.LOCATIONS_PATH:
            return json.dumps({"items": locations if page == 1 else []}).encode()
        lid = int(q["location_id"][0])
        return json.dumps({"items": prices_by_loc.get(lid, []) if page == 1 else []}).encode()
    return fetch


def test_country_quotas_balance_countries_and_ignore_unrequested_ones():
    locations = [loc(1, "FR", 500), loc(2, "FR", 400), loc(3, "DE", 50), loc(4, "IE", 20), loc(5, "US", 999)]
    prices = {1: [price(i, "FR", "A") for i in range(40)], 2: [price(100 + i, "FR", "B") for i in range(40)],
              3: [price(200 + i, "DE") for i in range(40)], 4: [price(300 + i, "IE") for i in range(10)],
              5: [price(400, "US")]}
    calls = []
    df = op.fetch_prices_by_location(["FR", "DE", "IE"], per_country_max=25, per_location_max=100,
                                     fetch=router(locations, prices, calls), sleep=lambda s: None)
    assert df["country"].value_counts().to_dict() == {"FR": 25, "DE": 25, "IE": 10}    # FR không áp đảo DE/IE
    assert not any("location_id=5" in c for c in calls)                                  # nước không yêu cầu: không tải
    assert "owner" not in df.columns


def test_per_location_cap_spreads_data_across_stores():
    locations = [loc(1, "FR", 500), loc(2, "FR", 400)]
    prices = {1: [price(i, "FR", "A") for i in range(50)], 2: [price(100 + i, "FR", "B") for i in range(50)]}
    df = op.fetch_prices_by_location(["FR"], per_country_max=60, per_location_max=30,
                                     fetch=router(locations, prices, []), sleep=lambda s: None)
    assert df["store"].value_counts().to_dict() == {"A": 30, "B": 30}


def test_only_eu_and_uk_countries_allowed():
    with pytest.raises(ValueError):
        op.fetch_prices_by_location(["US"], fetch=router([], {}, []), sleep=lambda s: None)
    with pytest.raises(ValueError):
        op.fetch_prices_by_location(["FR;DROP"], fetch=router([], {}, []), sleep=lambda s: None)


def test_location_id_must_be_a_positive_int():
    for bad in (0, -1, "1; rm", 1.5, True, 10 ** 12):
        with pytest.raises(ValueError):
            op.build_location_prices_url(bad, 1)
    assert "location_id=7" in op.build_location_prices_url(7, 1)


def test_invalid_locations_are_skipped():
    for bad in (loc(0, "FR"), loc(True, "FR"), loc(1, "FR", 0), {"id": 1}, "x", None):
        assert op.parse_location(bad, ["FR"]) is None
    assert op.parse_location(loc(3, "fr", 5), ["FR"]) == {"id": 3, "country": "FR", "price_count": 5}


def test_requests_are_throttled_between_calls():
    sleeps = []
    op.fetch_prices_by_location(["FR"], per_country_max=5, fetch=router([loc(1, "FR")], {1: [price(1, "FR")]}, []),
                                sleep=sleeps.append)
    assert sleeps and all(0 < s <= op.MIN_INTERVAL_SECONDS for s in sleeps)
