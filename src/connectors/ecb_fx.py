"""ECB reference exchange-rate connector (European Central Bank) -> CSV file in landing/ for the `fx_rate` contract.

The ECB publishes rates as "units of foreign currency per 1 EUR"; the connector converts them to eur_per_unit = 1 / rate.
Verified live on 2026-11-17 (GBP/EUR matched the ECB figure); the CSV layout is also covered by sample-data tests.
Sanity check: GBP/EUR is currently around 0.8-0.9 GBP per 1 EUR.

Same protections as the Open Prices connector: HTTPS to a single host and path, TLS verification, no redirects,
size/timeout limits, per-value validation, no personal data sent, files written via tmp + rename.
"""
import csv
import hashlib
import io
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from src.connectors.openprices import ConnectorError, _NoRedirect, read_capped, tls_context

ALLOWED_HOST = "data-api.ecb.europa.eu"
CURRENCIES = ("GBP", "SEK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN")
MAX_BYTES = 256 * 1024
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
USER_AGENT = "portfolio-research-client/1.0"
OUTPUT_COLUMNS = ["rate_date", "currency", "eur_per_unit", "source"]


def build_url(currencies: Iterable[str] = CURRENCIES) -> str:
    cur = list(currencies)
    if not cur or any(c not in CURRENCIES for c in cur):
        raise ValueError(f"currencies must be a non-empty subset of {CURRENCIES}")
    path = f"/service/data/EXR/D.{'+'.join(cur)}.EUR.SP00.A"
    q = urllib.parse.urlencode({"lastNObservations": 1, "format": "csvdata"})
    return validate_url(f"https://{ALLOWED_HOST}{path}?{q}")


def validate_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if (u.scheme != "https" or u.hostname != ALLOWED_HOST or u.username or u.password or u.port not in (None, 443)
            or not re.fullmatch(r"/service/data/EXR/D\.[A-Z+]+\.EUR\.SP00\.A", u.path)):
        raise ConnectorError(f"URL not allowed: {u.scheme}://{u.hostname}{u.path}")
    return url


def http_get(url: str) -> bytes:
    validate_url(url)
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=tls_context()))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    with opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
        validate_url(resp.geturl())
        return read_capped(resp, MAX_BYTES)


def parse_rates(payload: bytes) -> pd.DataFrame:
    """ECB CSV -> (rate_date, currency, eur_per_unit). Invalid rows are dropped; raises if none remain."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConnectorError(f"Not UTF-8: {exc}")
    rows: List[Dict] = []
    for rec in csv.DictReader(io.StringIO(text)):
        cur, date, val = (rec.get("CURRENCY") or "").upper(), rec.get("TIME_PERIOD") or "", rec.get("OBS_VALUE") or ""
        if cur not in CURRENCIES or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        try:
            per_eur = float(val)
        except ValueError:
            continue
        if not (0.0001 < per_eur < 100000):
            continue
        rows.append({"rate_date": date, "currency": cur, "eur_per_unit": round(1.0 / per_eur, 6), "source": "ecb-eurofxref"})
    if not rows:
        raise ConnectorError("No valid rates in response")
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def fetch_rates(currencies: Iterable[str] = CURRENCIES, fetch: Callable[[str], bytes] = http_get,
                sleep: Callable[[float], None] = time.sleep) -> pd.DataFrame:
    """Retry up to MAX_ATTEMPTS times on slow network/transient errors (5xx, timeout); 4xx and corrupt data stop immediately."""
    url, last = build_url(currencies), None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return parse_rates(fetch(url))
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise ConnectorError(f"HTTP {exc.code}")
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last = type(exc).__name__
        if attempt < MAX_ATTEMPTS - 1:
            sleep(2.0 ** (attempt + 1))
    raise ConnectorError(f"Network error after {MAX_ATTEMPTS} attempts: {last}")


def write_to_landing(df: pd.DataFrame, landing: Path) -> Path:
    landing.mkdir(parents=True, exist_ok=True)
    payload = df.sort_values(["currency", "rate_date"]).to_csv(index=False).encode("utf-8")
    final = landing / f"fx_ecb_{hashlib.sha256(payload).hexdigest()[:12]}.csv"
    if not final.exists():
        tmp = landing / f"_{final.name}.{os.getpid()}.tmp"
        tmp.write_bytes(payload)
        os.replace(tmp, final)
    return final


def main(argv: Optional[List[str]] = None) -> int:
    from src.config import DATA_DIR
    try:
        df = fetch_rates()
    except ConnectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out = write_to_landing(df, DATA_DIR / "landing")
    print(f"rates={len(df)} currencies={sorted(df['currency'])} file={out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
