"""Open Prices (Open Food Facts) connector -> CSV file in landing/, processed by the bronze->silver->gold pipeline.

Source: https://prices.openfoodfacts.org  |  Data licence: ODbL (credit "Open Food Facts contributors").

Responses from the internet are treated as UNTRUSTED. Protections:
    * HTTPS only, to a single allow-listed host (checked with urlparse; defeats userinfo/fake-subdomain tricks).
    * TLS certificate verification (default context); redirects are NOT followed; URLs found inside the returned data are never fetched
        (anti-SSRF; no images/receipts downloaded).
    * Limits: timeout, response size, page count, record count, 1 request/second, bounded retries.
    * JSON only; only allow-listed fields are kept. `owner`, `owner_comment`, `proof`, image URLs... are dropped (contributor
        identity is never stored).
    * Every value is validated (product code, price, currency, date, coordinates, country) and text is sanitised: control
        characters removed, CSV formula injection blocked (=, +, -, @), '|' replaced so tombstone keys stay unambiguous.
    * No personal information is sent: generic User-Agent, no cookies, no tokens. Files are written via tmp + rename.
    * Downloaded data is never executed or `eval`ed; it only goes through json.loads and the pipeline's data contracts.
"""
import argparse
import hashlib
import json
import math
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

ALLOWED_HOST = "prices.openfoodfacts.org"
API_PATH = "/api/v1/prices"
LOCATIONS_PATH = "/api/v1/locations"
ALLOWED_PATHS = (API_PATH, LOCATIONS_PATH)
USER_AGENT = "portfolio-research-client/1.0"
ALLOWED_CURRENCIES = ("GBP", "EUR", "SEK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN")
# EU27 + United Kingdom: only these countries are accepted as a country filter
ALLOWED_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE GB".split())
DEFAULT_COUNTRIES = ("GB", "FR", "DE", "IE")
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
PAGE_SIZE = 100
TIMEOUT_SECONDS = 20
MIN_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 3
OUTPUT_COLUMNS = ["product_code", "product_name", "category", "price", "currency", "store", "city",
                  "country", "lat", "lon", "observed_at", "source"]


class ConnectorError(RuntimeError):
    pass


class ResponseTooLarge(ConnectorError):
    pass


class HTTPStatusError(ConnectorError):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


# ------------------------------------------------------------------ network
def validate_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname != ALLOWED_HOST or u.username or u.password or u.port not in (None, 443):
        raise ConnectorError(f"URL not allowed: {u.scheme}://{u.hostname}")
    if u.path not in ALLOWED_PATHS:
        raise ConnectorError(f"Path not allowed: {u.path}")
    return url


def build_url(currency: str, page: int, size: int = PAGE_SIZE) -> str:
    if currency not in ALLOWED_CURRENCIES:
        raise ValueError(f"currency must be one of {ALLOWED_CURRENCIES}")
    if not (1 <= size <= PAGE_SIZE) or not (1 <= page <= 10_000):
        raise ValueError("page/size out of range")
    q = urllib.parse.urlencode({"currency": currency, "size": size, "page": page, "order_by": "-date"})
    return validate_url(f"https://{ALLOWED_HOST}{API_PATH}?{q}")


def build_locations_url(page: int, size: int = PAGE_SIZE) -> str:
    if not (1 <= size <= PAGE_SIZE) or not (1 <= page <= 10_000):
        raise ValueError("page/size out of range")
    return validate_url(f"https://{ALLOWED_HOST}{LOCATIONS_PATH}?" + urllib.parse.urlencode({"size": size, "page": page}))


def build_location_prices_url(location_id: int, page: int, size: int = PAGE_SIZE) -> str:
    if isinstance(location_id, bool) or not isinstance(location_id, int) or not (0 < location_id < 10 ** 9):
        raise ValueError("location_id must be a positive integer")
    if not (1 <= size <= PAGE_SIZE) or not (1 <= page <= 10_000):
        raise ValueError("page/size out of range")
    q = urllib.parse.urlencode({"location_id": location_id, "size": size, "page": page, "order_by": "-date"})
    return validate_url(f"https://{ALLOWED_HOST}{API_PATH}?{q}")


def tls_context() -> ssl.SSLContext:
    """    TLS context that ALWAYS verifies certificate and hostname. Uses the certifi root bundle when available, because the
        Windows certificate store can lack an intermediate certificate (CERTIFICATE_VERIFY_FAILED). Never disable verification."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ConnectorError("Redirects are not followed")


def read_capped(stream, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ResponseTooLarge(f"Response larger than {limit} bytes")
    return data


def http_get(url: str) -> bytes:
    validate_url(url)
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=tls_context()))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
        validate_url(resp.geturl().split("?")[0])
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise ConnectorError(f"Unexpected content type: {ctype[:50]}")
        return read_capped(resp)


def fetch_json(url: str, fetch: Callable[[str], bytes], sleep: Callable[[float], None]) -> dict:
    last: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            data = json.loads(fetch(url))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                retry_after = 0.0
                try:
                    retry_after = min(60.0, float(exc.headers.get("Retry-After", 0))) if exc.headers else 0.0
                except ValueError:
                    pass
                sleep(max(retry_after, 2.0 ** attempt))
                last = exc
                continue
            raise HTTPStatusError(exc.code)
        except (urllib.error.URLError, TimeoutError) as exc:
            sleep(2.0 ** attempt)
            last = exc
            continue
        except ValueError as exc:                           # corrupt JSON: do not retry
            raise ConnectorError(f"Invalid JSON: {exc}")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ConnectorError("Unexpected response shape")
        return data
    raise ConnectorError(f"Giving up after {MAX_RETRIES} attempts: {type(last).__name__}")


# ------------------------------------------------------------------ sanitising & value checks
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def sanitize_text(value, max_len: int = 200) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = unicodedata.normalize("NFKC", str(value)).translate({9: " ", 10: " ", 13: " "})
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C") or ch in " ")
    text = re.sub(r"\s+", " ", text).strip().replace("|", "/")[:max_len]
    if not text:
        return None
    if text.startswith(_FORMULA_START):
        text = "'" + text                                   # block CSV/Excel formula injection
    return text


def _number(value, lo: float, hi: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) and lo < f < hi else None


def parse_item(item: dict, countries: Iterable[str] = DEFAULT_COUNTRIES) -> Optional[Dict]:
    """One API record -> clean row with allow-listed fields; None if invalid or out of scope."""
    if not isinstance(item, dict) or item.get("duplicate_of") is not None:
        return None
    loc = item.get("location") if isinstance(item.get("location"), dict) else {}
    country = str(loc.get("osm_address_country_code") or "").upper()
    if country not in set(countries):
        return None
    code = str(item.get("product_code") or "")
    currency = str(item.get("currency") or "").upper()
    date = str(item.get("date") or "")
    price = _number(item.get("price"), 0.0, 1_000_000.0)
    if (not re.fullmatch(r"\d{4,14}", code) or currency not in ALLOWED_CURRENCIES
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or price is None):
        return None
    product = item.get("product") if isinstance(item.get("product"), dict) else {}
    cats = product.get("categories_tags") if isinstance(product.get("categories_tags"), list) else []
    category = sanitize_text((cats[-1] if cats else item.get("category_tag")), 80)
    lat, lon = _number(loc.get("osm_lat"), -90.0, 90.0), _number(loc.get("osm_lon"), -180.0, 180.0)
    return {
        "product_code": code,
        "product_name": sanitize_text(item.get("product_name") or product.get("product_name")),
        "category": category,
        "price": round(price, 4),
        "currency": currency,
        "store": sanitize_text(loc.get("osm_name"), 100),
        "city": sanitize_text(loc.get("osm_address_city"), 100),
        "country": country,
        "lat": lat,
        "lon": lon,
        "observed_at": date,
        "source": "openfoodfacts-prices",
    }


def parse_location(item: dict, countries: Iterable[str]) -> Optional[Dict]:
    if not isinstance(item, dict):
        return None
    lid, count = item.get("id"), item.get("price_count")
    country = str(item.get("osm_address_country_code") or "").upper()
    if (isinstance(lid, bool) or not isinstance(lid, int) or lid <= 0 or country not in set(countries)
            or isinstance(count, bool) or not isinstance(count, int) or count <= 0):
        return None
    return {"id": lid, "country": country, "price_count": count}


class RequestBudgetExceeded(ConnectorError):
    pass


class _Throttle:
    """Minimum gap between requests, plus a hard cap on the TOTAL number of requests (prevents runaway scans when data never meets the quota)."""

    def __init__(self, sleep: Callable[[float], None], max_requests: int = 10 ** 9):
        self.sleep, self.last, self.count, self.max_requests = sleep, 0.0, 0, max_requests

    def wait(self) -> None:
        if self.count >= self.max_requests:
            raise RequestBudgetExceeded(f"Request budget of {self.max_requests} exhausted")
        self.count += 1
        gap = MIN_INTERVAL_SECONDS - (time.monotonic() - self.last)
        if self.last and gap > 0:
            self.sleep(gap)
        self.last = time.monotonic()


# ------------------------------------------------------------------ fetching data
def fetch_prices(currencies: Iterable[str] = ("GBP", "EUR"), countries: Iterable[str] = DEFAULT_COUNTRIES,
                 max_records: int = 5000, max_pages: int = 60,
                 fetch: Callable[[str], bytes] = http_get,
                 sleep: Callable[[float], None] = time.sleep) -> pd.DataFrame:
    """max_records: total rows kept; max_pages: max pages scanned PER currency (the API cannot filter by country)."""
    countries = tuple(c.upper() for c in countries)
    rows: List[Dict] = []
    last_call = 0.0
    for currency in currencies:
        for page in range(1, max_pages + 1):
            wait = MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)
            if last_call and wait > 0:
                sleep(wait)
            last_call = time.monotonic()
            try:
                data = fetch_json(build_url(currency, page), fetch, sleep)
            except HTTPStatusError as exc:
                if exc.code == 404 and page > 1:       # the API returns 404 past the last page: no more data
                    break
                raise
            items = data["items"]
            if not items:
                break
            for it in items:
                row = parse_item(it, countries)
                if row:
                    rows.append(row)
                    if len(rows) >= max_records:
                        break
            if len(rows) >= max_records:
                break
        if len(rows) >= max_records:
            break
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def fetch_prices_by_location(countries: Iterable[str] = DEFAULT_COUNTRIES, per_country_max: int = 1500,
                             per_location_max: int = 300, max_location_pages: int = 100,
                             fetch: Callable[[str], bytes] = http_get,
                             sleep: Callable[[float], None] = time.sleep,
                             log: Callable[[str], None] = lambda m: None, max_requests: int = 600,
                             max_locations_per_country: int = 60) -> pd.DataFrame:
    """Because the API cannot filter by country: (1) scan the location list and filter countries client-side,
    (2) fetch prices per location_id with a quota per country and per location (so one store cannot dominate)."""
    countries = tuple(c.upper() for c in countries)
    bad = [c for c in countries if c not in ALLOWED_COUNTRIES]
    if bad:
        raise ValueError(f"Countries not allowed: {bad}")
    th = _Throttle(sleep, max_requests)
    locs: Dict[str, List[Dict]] = {c: [] for c in countries}
    for page in range(1, max_location_pages + 1):
        th.wait()
        try:
            data = fetch_json(build_locations_url(page), fetch, sleep)
        except HTTPStatusError as exc:
            if exc.code == 404 and page > 1:
                break
            raise
        if not data["items"]:
            break
        for it in data["items"]:
            loc = parse_location(it, countries)
            if loc:
                locs[loc["country"]].append(loc)

    log("locations found: " + ", ".join(f"{c}={len(v)}" for c, v in locs.items()))
    rows: List[Dict] = []
    try:
        _collect(countries, locs, rows, th, fetch, sleep, per_country_max, per_location_max, max_locations_per_country, log)
    except ConnectorError as exc:                   # request budget exhausted or persistent network error: keep what was collected
        log(f"STOPPED EARLY: {exc}; keeping {len(rows)} rows already collected")
        partial = True
    else:
        partial = False
    out = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    out.attrs["partial"] = partial
    return out


def _collect(countries, locs, rows, th, fetch, sleep, per_country_max, per_location_max, max_locations_per_country, log):
    for country in countries:
        got = 0
        for loc in sorted(locs[country], key=lambda x: -x["price_count"])[:max_locations_per_country]:
            taken = 0
            for page in range(1, -(-per_location_max // PAGE_SIZE) + 1):
                if got >= per_country_max or taken >= per_location_max:
                    break
                th.wait()
                try:
                    data = fetch_json(build_location_prices_url(loc["id"], page), fetch, sleep)
                except HTTPStatusError as exc:
                    if exc.code == 404 and page > 1:
                        break
                    raise
                if not data["items"]:
                    break
                for it in data["items"]:
                    row = parse_item(it, countries)
                    if row and row["country"] == country:
                        rows.append(row)
                        got += 1
                        taken += 1
                        if got >= per_country_max or taken >= per_location_max:
                            break
            if got >= per_country_max:
                break
        log(f"{country}: {got} rows")


def write_to_landing(df: pd.DataFrame, landing: Path) -> Optional[Path]:
    """Write the CSV into landing: tmp + rename, content-addressed name (re-downloading the same data creates no duplicate file)."""
    if df.empty:
        return None
    landing.mkdir(parents=True, exist_ok=True)
    df = df.drop_duplicates().sort_values(["country", "product_code", "store", "observed_at"]).reset_index(drop=True)
    payload = df.to_csv(index=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:12]
    final = landing / f"openprices_{digest}.csv"
    if final.exists():
        return final
    tmp = landing / f"_{final.name}.{os.getpid()}.tmp"
    tmp.write_bytes(payload)
    os.replace(tmp, final)
    return final


def main(argv: Optional[List[str]] = None) -> int:
    from src.config import DATA_DIR
    ap = argparse.ArgumentParser(prog="python -m src.connectors.openprices")
    ap.add_argument("--mode", choices=["by-location", "currency"], default="by-location")
    ap.add_argument("--per-country", type=int, default=1500)
    ap.add_argument("--max-requests", type=int, default=600, help="hard cap on total HTTP requests")
    ap.add_argument("--currencies", nargs="+", default=["GBP", "EUR"], choices=list(ALLOWED_CURRENCIES))
    ap.add_argument("--countries", nargs="+", default=list(DEFAULT_COUNTRIES))
    ap.add_argument("--max-records", type=int, default=5000)
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args(argv)
    if not (1 <= args.max_records <= 100_000 and 1 <= args.max_pages <= 500):
        ap.error("max-records must be 1..100000 and max-pages 1..500")
    try:
        if args.mode == "by-location":
            if not (1 <= args.per_country <= 20_000):
                ap.error("per-country must be 1..20000")
            df = fetch_prices_by_location(args.countries, per_country_max=args.per_country, max_requests=args.max_requests,
                                          log=lambda m: print(m, file=sys.stderr, flush=True))
        else:
            df = fetch_prices(args.currencies, args.countries, args.max_records, args.max_pages)
    except (ConnectorError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out = write_to_landing(df, DATA_DIR / "landing")
    print(("PARTIAL RESULT (stopped early); " if df.attrs.get("partial") else "") + f"kept {len(df)} rows; countries={sorted(df['country'].unique()) if len(df) else []}; file={out.name if out else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
