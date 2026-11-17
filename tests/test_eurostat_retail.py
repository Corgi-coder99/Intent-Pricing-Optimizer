import json
import urllib.error

import pytest

from src.connectors import eurostat_retail as es
from src.connectors.openprices import ConnectorError


def jsonstat(values_by_key):
    """Build a JSON-stat 2.0 payload with the real structure: id=[freq,nace_r2,indic_sb,geo,time], value is a dict keyed by flat position."""
    geos, naces, inds, times = ["FR", "UK"], ["G47", "G4711"], list(es.INDICATORS), ["2019", "2018"]
    ids = ["freq", "nace_r2", "indic_sb", "geo", "time"]
    size = [1, len(naces), len(inds), len(geos), len(times)]
    dims = {"freq": ["A"], "nace_r2": naces, "indic_sb": inds, "geo": geos, "time": times}
    value = {}
    for (g, n, t, i), v in values_by_key.items():
        pos = 0
        for k, s in zip(ids, size):
            idx = {"freq": 0, "nace_r2": naces.index(n), "indic_sb": inds.index(i), "geo": geos.index(g), "time": times.index(t)}[k]
            pos = pos * s + idx
        value[str(pos)] = v
    return json.dumps({"id": ids, "size": size, "value": value,
                       "dimension": {k: {"category": {"index": {c: j for j, c in enumerate(v)}}} for k, v in dims.items()}}).encode()


DATA = {
    ("FR", "G4711", "2019", "V12110"): 227685.6, ("FR", "G4711", "2019", "V12130"): 47857.6,
    ("FR", "G4711", "2019", "V92110"): 2.7,
    ("UK", "G4711", "2018", "V12110"): 197708.9, ("UK", "G4711", "2018", "V12130"): 62891.1,   # UK only has 2018
    ("FR", "G47", "2019", "V12110"): 481070.1, ("FR", "G47", "2019", "V12130"): 137434.4,
}


def test_parse_and_table_use_latest_valid_year_and_map_uk_to_gb():
    df = es.build_table(es.parse_jsonstat(jsonstat(DATA)), "2026-11-17").set_index(["country", "nace_r2"])
    assert df.loc[("FR", "G4711"), "gross_margin_pct"] == pytest.approx(47857.6 / 227685.6 * 100, abs=0.01)
    assert df.loc[("FR", "G4711"), "year"] == 2019 and df.loc[("GB", "G4711"), "year"] == 2018
    assert df.loc[("GB", "G4711"), "gross_margin_pct"] == pytest.approx(31.81, abs=0.01)
    assert df.loc[("FR", "G47"), "gross_operating_rate_pct"] != df.loc[("FR", "G47"), "gross_operating_rate_pct"]  # NaN: not provided


def test_implausible_rows_are_dropped():
    bad = dict(DATA)
    bad[("FR", "G47", "2019", "V12130")] = 999999999.0                  # gross margin larger than turnover
    df = es.build_table(es.parse_jsonstat(jsonstat(bad)), "2026-11-17")
    assert not ((df["country"] == "FR") & (df["nace_r2"] == "G47")).any()


@pytest.mark.parametrize("payload", [b"", b"not json", b"{}", b'{"error": {"status": 400}}',
                                     json.dumps({"id": ["geo"], "size": [1], "value": {}, "dimension": {"geo": {"category": {"index": {"FR": 0}}}}}).encode()])
def test_bad_payloads_raise(payload):
    with pytest.raises(ConnectorError):
        es.build_table(es.parse_jsonstat(payload), "2026-11-17")


def test_url_building_and_allowlist():
    assert "geo=FR" in es.build_url(["FR"]) and es.build_url(["FR"]).startswith("https://ec.europa.eu/eurostat/")
    for bad in ([], ["US"], ["FR;x"]):
        with pytest.raises(ValueError):
            es.build_url(bad)
    with pytest.raises(ValueError):
        es.build_url(["FR"], naces=["G46"])
    for url in ("http://ec.europa.eu" + es.PATH, "https://evil.com" + es.PATH, "https://ec.europa.eu.evil.com" + es.PATH,
                "https://ec.europa.eu@evil.com" + es.PATH, "https://ec.europa.eu/other"):
        with pytest.raises(ConnectorError):
            es.validate_url(url)


def test_retries_transient_errors_only():
    n = {"c": 0}

    def flaky(url):
        n["c"] += 1
        if n["c"] < 3:
            raise urllib.error.URLError("slow")
        return jsonstat(DATA)

    assert len(es.fetch_table(["FR"], fetch=flaky, sleep=lambda s: None)) >= 2

    def forbidden(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    with pytest.raises(ConnectorError):
        es.fetch_table(["FR"], fetch=forbidden, sleep=lambda s: None)


def test_pandemic_year_2020_is_excluded_for_comparability():
    with_2020 = dict(DATA)
    with_2020[("FR", "G4711", "2020", "V12110")] = 1000.0
    with_2020[("FR", "G4711", "2020", "V12130")] = 900.0               # abnormal 90% margin in 2020
    # the real payload has 2020 on the time axis; here the filter is applied directly to the value dict
    df = es.build_table(with_2020, "2026-11-17").set_index(["country", "nace_r2"])
    assert df.loc[("FR", "G4711"), "year"] == 2019
