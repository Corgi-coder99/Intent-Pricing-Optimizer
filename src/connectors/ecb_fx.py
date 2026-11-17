"""Connector tỷ giá tham chiếu của Ngân hàng Trung ương châu Âu (ECB) -> file CSV ở landing/ cho hợp đồng `fx_rate`.

ECB công bố tỷ giá dạng "số đơn vị ngoại tệ cho 1 EUR"; connector đổi thành eur_per_unit = 1 / tỷ giá.
CHƯA kiểm chứng với phản hồi thật: định dạng CSV được dựng theo tài liệu của ECB và kiểm thử bằng dữ liệu mẫu.
Chạy lần đầu ngoài thực tế, hãy kiểm tra số liệu (GBP/EUR hiện quanh 0,8-0,9 đơn vị GBP cho 1 EUR).

Lớp bảo vệ giống connector Open Prices: HTTPS tới đúng một host và một đường dẫn, kiểm tra TLS, không theo chuyển hướng,
giới hạn dung lượng/timeout, kiểm tra từng giá trị, không gửi thông tin cá nhân, ghi file bằng tmp + rename.
"""
import csv
import hashlib
import io
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from src.connectors.openprices import ConnectorError, _NoRedirect, read_capped

ALLOWED_HOST = "data-api.ecb.europa.eu"
CURRENCIES = ("GBP", "SEK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN")
MAX_BYTES = 256 * 1024
TIMEOUT_SECONDS = 20
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
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    with opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
        validate_url(resp.geturl())
        return read_capped(resp, MAX_BYTES)


def parse_rates(payload: bytes) -> pd.DataFrame:
    """CSV của ECB -> (rate_date, currency, eur_per_unit). Bỏ dòng không hợp lệ; lỗi nếu không còn dòng nào."""
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


def fetch_rates(currencies: Iterable[str] = CURRENCIES, fetch: Callable[[str], bytes] = http_get) -> pd.DataFrame:
    try:
        return parse_rates(fetch(build_url(currencies)))
    except urllib.error.HTTPError as exc:
        raise ConnectorError(f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectorError(f"Network error: {type(exc).__name__}")


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
