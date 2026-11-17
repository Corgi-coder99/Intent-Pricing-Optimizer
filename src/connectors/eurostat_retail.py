"""Connector Eurostat: biên gộp ngành bán lẻ theo quốc gia (thống kê doanh nghiệp, bộ dữ liệu sbs_na_dt_r2, 2005-2020).

Chỉ số dùng: V12110 doanh thu (triệu EUR, chưa gồm VAT), V12130 biên gộp trên hàng bán lại (triệu EUR),
V92110 tỷ suất lợi nhuận hoạt động gộp (%). Biên gộp % = V12130 / V12110 * 100, tính bằng mã.
Nhóm NACE: G47 (bán lẻ nói chung) và G4711 (cửa hàng không chuyên, chủ yếu thực phẩm/đồ uống).
Đây là số của CẢ NGÀNH theo nước, không phải giá vốn của công ty hay sản phẩm nào.
Vương quốc Anh (mã UK) chỉ có đến 2018 vì Eurostat ngừng thu thập sau Brexit. Năm 2020 bị loại (MAX_YEAR=2019).

Lớp bảo vệ như các connector khác: HTTPS tới một host và một đường dẫn, TLS xác minh, không theo chuyển hướng, giới hạn dung lượng,
tham số kiểm tra theo danh sách cho phép, kiểm tra từng giá trị, không gửi thông tin cá nhân, ghi file bằng tmp + rename.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from src.connectors.openprices import ConnectorError, _NoRedirect, read_capped, tls_context

ALLOWED_HOST = "ec.europa.eu"
PATH = "/eurostat/api/dissemination/statistics/1.0/data/sbs_na_dt_r2"
USER_AGENT = "portfolio-research-client/1.0"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3
GEOS = frozenset("AT BE BG HR CY CZ DK EE FI FR DE EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE UK".split())
NACES = ("G47", "G4711")
INDICATORS = ("V12110", "V12130", "V92110")
YEARS = tuple(str(y) for y in range(2015, 2021))
DEFAULT_GEOS = ("FR", "DE", "IE", "ES", "IT", "NL", "BE", "UK")
OUTPUT_COLUMNS = ["country", "nace_r2", "year", "turnover_m_eur", "gross_margin_m_eur", "gross_margin_pct",
                  "gross_operating_rate_pct", "source", "retrieved_on"]
SOURCE = "eurostat:sbs_na_dt_r2 (V12110, V12130, V92110)"


def build_url(geos: Iterable[str], naces: Iterable[str] = NACES, years: Iterable[str] = YEARS) -> str:
    geos, naces, years = list(geos), list(naces), list(years)
    if not geos or any(g not in GEOS for g in geos):
        raise ValueError("geo must be a non-empty subset of the allowed Eurostat country codes")
    if any(n not in NACES for n in naces) or not naces:
        raise ValueError(f"nace_r2 must be a subset of {NACES}")
    if any(y not in YEARS for y in years) or not years:
        raise ValueError(f"years must be a subset of {YEARS}")
    params = [("format", "JSON"), ("lang", "EN")] + [("geo", g) for g in geos] + [("nace_r2", n) for n in naces] \
        + [("indic_sb", i) for i in INDICATORS] + [("time", y) for y in years]
    return validate_url(f"https://{ALLOWED_HOST}{PATH}?{urllib.parse.urlencode(params)}")


def validate_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if (u.scheme != "https" or u.hostname != ALLOWED_HOST or u.username or u.password or u.port not in (None, 443)
            or u.path != PATH):
        raise ConnectorError(f"URL not allowed: {u.scheme}://{u.hostname}{u.path}")
    return url


def http_get(url: str) -> bytes:
    validate_url(url)
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=tls_context()))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
        validate_url(resp.geturl().split("?")[0])
        return read_capped(resp, MAX_BYTES)


def parse_jsonstat(payload: bytes) -> Dict[tuple, float]:
    """JSON-stat 2.0 -> {(geo, nace_r2, time, indic_sb): value}. Lỗi nếu cấu trúc không đúng như kỳ vọng."""
    try:
        d = json.loads(payload)
        if "error" in d:
            raise ConnectorError(f"Eurostat error: {str(d['error'])[:120]}")
        ids, size, values = d["id"], d["size"], d["value"]
        cats = {k: d["dimension"][k]["category"]["index"] for k in ids}
        need = {"geo", "nace_r2", "time", "indic_sb"}
        if not need <= set(ids) or not isinstance(values, dict):
            raise ConnectorError("Unexpected JSON-stat structure")
    except (ValueError, KeyError, TypeError) as exc:
        raise ConnectorError(f"Invalid JSON-stat: {type(exc).__name__}")
    inv = {k: {v: kk for kk, v in cats[k].items()} for k in ids}
    out: Dict[tuple, float] = {}
    for pos, val in values.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        p, coords = int(pos), {}
        for k, s in reversed(list(zip(ids, size))):
            coords[k] = inv[k].get(p % s)
            p //= s
        out[(coords["geo"], coords["nace_r2"], coords["time"], coords["indic_sb"])] = float(val)
    return out


MAX_YEAR = 2019   # bỏ 2020 (năm đại dịch làm lệch doanh số/biên bán lẻ) để các nước so sánh được với nhau


def build_table(values: Dict[tuple, float], retrieved_on: str, max_year: int = MAX_YEAR) -> pd.DataFrame:
    """Với mỗi (nước, NACE) lấy năm MỚI NHẤT không quá max_year có đủ doanh thu và biên gộp hợp lệ; biên gộp % tính bằng mã."""
    rows: List[Dict] = []
    keys = {(g, n) for (g, n, _t, _i) in values}
    for g, n in sorted(keys):
        years = sorted({t for (gg, nn, t, _i) in values if (gg, nn) == (g, n) and int(t) <= max_year}, reverse=True)
        for y in years:
            turnover, margin = values.get((g, n, y, "V12110")), values.get((g, n, y, "V12130"))
            if not turnover or margin is None or turnover <= 0 or not (0 <= margin <= turnover):
                continue
            rows.append({"country": "GB" if g == "UK" else g, "nace_r2": n, "year": int(y),
                         "turnover_m_eur": turnover, "gross_margin_m_eur": margin,
                         "gross_margin_pct": round(margin / turnover * 100, 2),
                         "gross_operating_rate_pct": values.get((g, n, y, "V92110")),
                         "source": SOURCE, "retrieved_on": retrieved_on})
            break
    if not rows:
        raise ConnectorError("No usable rows in response")
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def fetch_table(geos: Iterable[str] = DEFAULT_GEOS, fetch: Callable[[str], bytes] = http_get,
                sleep: Callable[[float], None] = time.sleep, today: Optional[str] = None) -> pd.DataFrame:
    url, last = build_url(geos), None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return build_table(parse_jsonstat(fetch(url)), today or time.strftime("%Y-%m-%d"))
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise ConnectorError(f"HTTP {exc.code}")
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last = type(exc).__name__
        if attempt < MAX_ATTEMPTS - 1:
            sleep(2.0 ** (attempt + 1))
    raise ConnectorError(f"Network error after {MAX_ATTEMPTS} attempts: {last}")


def write_reference(df: pd.DataFrame, path: Path) -> None:
    header = ("# Biên gộp ngành bán lẻ theo quốc gia, nguồn Eurostat (sbs_na_dt_r2). Số của cả ngành, không phải của công ty nào.\n"
              "# gross_margin_pct = gross_margin_m_eur / turnover_m_eur * 100 (tính bằng mã). Doanh thu chưa gồm VAT.\n"
              "# Tạo bằng: python -m src.connectors.eurostat_retail\n")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(header + df.to_csv(index=False), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: Optional[List[str]] = None) -> int:
    path = Path(__file__).resolve().parent.parent / "pipeline" / "reference" / "eurostat_retail_margin.csv"
    try:
        df = fetch_table()
    except ConnectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    write_reference(df, path)
    print(f"wrote {len(df)} rows to {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
