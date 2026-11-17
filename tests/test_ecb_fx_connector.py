import urllib.error

import pytest

from src.connectors import ecb_fx
from src.connectors.openprices import ConnectorError

SAMPLE = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-11-14,0.8600,A\n"
    "EXR.D.SEK.EUR.SP00.A,D,SEK,EUR,SP00,A,2026-11-14,11.0000,A\n"
    "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-11-14,1.1000,A\n"          # out of scope
    "EXR.D.PLN.EUR.SP00.A,D,PLN,EUR,SP00,A,not-a-date,4.2,A\n"             # corrupt date
    "EXR.D.DKK.EUR.SP00.A,D,DKK,EUR,SP00,A,2026-11-14,-7.4,A\n"            # negative value
    "EXR.D.CZK.EUR.SP00.A,D,CZK,EUR,SP00,A,2026-11-14,abc,A\n"
)


def test_parse_converts_to_eur_per_unit_and_drops_bad_rows():
    df = ecb_fx.parse_rates(SAMPLE.encode())
    assert dict(zip(df["currency"], df["eur_per_unit"])) == {"GBP": pytest.approx(1 / 0.86, rel=1e-5),
                                                              "SEK": pytest.approx(1 / 11, rel=1e-5)}
    assert list(df.columns) == ecb_fx.OUTPUT_COLUMNS


@pytest.mark.parametrize("payload", [b"", b"garbage", b"a,b\n1,2\n", b"\xff\xfe\x00"])
def test_unusable_payload_raises(payload):
    with pytest.raises(ConnectorError):
        ecb_fx.parse_rates(payload)


@pytest.mark.parametrize("url", [
    "http://data-api.ecb.europa.eu/service/data/EXR/D.GBP.EUR.SP00.A",
    "https://evil.com/service/data/EXR/D.GBP.EUR.SP00.A",
    "https://data-api.ecb.europa.eu.evil.com/service/data/EXR/D.GBP.EUR.SP00.A",
    "https://data-api.ecb.europa.eu@evil.com/service/data/EXR/D.GBP.EUR.SP00.A",
    "https://data-api.ecb.europa.eu/service/data/ICP/anything",
    "https://data-api.ecb.europa.eu/service/data/EXR/D.GBP.EUR.SP00.A/../../x",
])
def test_url_allowlist(url):
    with pytest.raises(ConnectorError):
        ecb_fx.validate_url(url)


def test_build_url_only_accepts_known_currencies():
    assert "D.GBP+SEK.EUR" in ecb_fx.build_url(["GBP", "SEK"])
    for bad in ([], ["USD"], ["GBP;x"]):
        with pytest.raises(ValueError):
            ecb_fx.build_url(bad)


def test_network_errors_become_connector_errors():
    def down(url):
        raise urllib.error.URLError("no route")

    with pytest.raises(ConnectorError):
        ecb_fx.fetch_rates(["GBP"], fetch=down)


def test_landing_file_is_content_addressed(tmp_path):
    df = ecb_fx.parse_rates(SAMPLE.encode())
    a = ecb_fx.write_to_landing(df, tmp_path)
    assert ecb_fx.write_to_landing(df, tmp_path) == a and len(list(tmp_path.iterdir())) == 1


def test_tls_verification_is_never_disabled():
    import ssl
    from src.connectors.openprices import tls_context
    ctx = tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_transient_failures_are_retried_but_client_errors_are_not():
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError()
        return SAMPLE.encode()

    assert len(ecb_fx.fetch_rates(["GBP", "SEK"], fetch=flaky, sleep=lambda s: None)) == 2 and calls["n"] == 3

    def forbidden(url):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 403, "no", {}, None)

    calls["n"] = 0
    with pytest.raises(ConnectorError):
        ecb_fx.fetch_rates(["GBP"], fetch=forbidden, sleep=lambda s: None)
    assert calls["n"] == 1
