"""Data contracts: for each record type, the required columns, types, dedup key and validity rules.

`classify` detects the record type from a source file's columns (bronze), `clean` coerces types + validates
+ deduplicates within the batch (silver). Bad rows are split off with a `reject_reason`, never silently deleted.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

Check = Tuple[str, Callable[[pd.DataFrame], pd.Series]]   # (rule name, function returning the VALID mask)


@dataclass
class Contract:
    name: str
    key: List[str]
    required: List[str]
    dtypes: Dict[str, str]                      # str | float | int | datetime
    checks: List[Check] = field(default_factory=list)
    normalize: Optional[Callable[[pd.DataFrame], pd.DataFrame]] = None
    strict_columns: bool = False        # True: columns outside the contract are dropped immediately and never reach silver


def _ok(s: pd.Series) -> pd.Series:
    return s.fillna(False).astype(bool)


def _session_norm(df: pd.DataFrame) -> pd.DataFrame:
    if "discount_pct" not in df.columns:
        df["discount_pct"] = ((1 - df["offered_price"] / df["base_price"]) * 100).round(2)
    if "competitor_price_ratio" not in df.columns:
        df["competitor_price_ratio"] = (df["offered_price"] / df["competitor_price"]).round(3)
    return df


def _price_norm(df: pd.DataFrame) -> pd.DataFrame:
    df["currency"] = df["currency"].str.upper()
    df["country"] = df["country"].str.upper()
    for c in ("store", "city"):
        df[c] = df[c].fillna("unknown") if c in df.columns else "unknown"
    return df


CONTRACTS: Dict[str, Contract] = {
    "session": Contract(
        name="session",
        key=["session_id"],
        required=["session_id", "product_id", "category", "region", "base_price", "competitor_price",
                  "offered_price", "session_views", "cart_additions_7d", "recent_review_text", "converted"],
        dtypes={"session_id": "str", "product_id": "str", "product_name": "str", "category": "str",
                "region": "str", "recent_review_text": "str", "base_price": "float",
                "competitor_price": "float", "offered_price": "float", "discount_pct": "float",
                "competitor_price_ratio": "float", "lat": "float", "lon": "float",
                "session_views": "float", "cart_additions_7d": "float", "converted": "int"},
        checks=[
            ("price_not_positive", lambda d: _ok((d.offered_price > 0) & (d.base_price > 0) & (d.competitor_price > 0))),
            ("converted_not_binary", lambda d: _ok(d.converted.isin([0, 1]))),
            ("negative_activity", lambda d: _ok((d.session_views >= 0) & (d.cart_additions_7d >= 0))),
            ("geo_out_of_range", lambda d: _ok(d.lat.between(-90, 90) | d.lat.isna()) & _ok(d.lon.between(-180, 180) | d.lon.isna())
             if "lat" in d.columns and "lon" in d.columns else pd.Series(True, index=d.index)),
        ],
        normalize=_session_norm,
    ),
    "price_observation": Contract(
        name="price_observation",
        key=["product_code", "store", "country", "city", "observed_at", "price"],
        required=["product_code", "price", "currency", "country", "observed_at"],
        dtypes={"product_code": "str", "product_name": "str", "category": "str", "store": "str",
                "city": "str", "country": "str", "currency": "str", "price": "float",
                "lat": "float", "lon": "float", "observed_at": "datetime"},
        checks=[
            ("price_out_of_range", lambda d: _ok((d.price > 0) & (d.price < 1_000_000))),
            ("bad_currency", lambda d: _ok(d.currency.str.fullmatch(r"[A-Za-z]{3}"))),
            ("observed_in_future", lambda d: _ok(d.observed_at <= pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1))),
        ],
        normalize=_price_norm,
    ),
    # FX rate: eur_per_unit = EUR received for 1 unit of the currency (GBP -> about 1.17)
    "fx_rate": Contract(
        name="fx_rate",
        key=["rate_date", "currency"],
        required=["rate_date", "currency", "eur_per_unit"],
        dtypes={"rate_date": "datetime", "currency": "str", "eur_per_unit": "float", "source": "str"},
        checks=[
            ("rate_out_of_range", lambda d: _ok((d.eur_per_unit > 0) & (d.eur_per_unit < 10_000))),
            ("bad_currency", lambda d: _ok(d.currency.str.fullmatch(r"[A-Za-z]{3}"))),
        ],
        normalize=lambda df: df.assign(currency=df["currency"].str.upper()),
    ),
    # Branch cost and selling price (INTERNAL company data); own_price is the VAT-inclusive selling price
    "branch_cost": Contract(
        name="branch_cost",
        key=["product_code", "country"],
        required=["product_code", "country", "unit_cost", "currency"],
        dtypes={"product_code": "str", "country": "str", "currency": "str", "unit_cost": "float",
                "own_price": "float", "product_name": "str"},
        checks=[
            ("cost_out_of_range", lambda d: _ok((d.unit_cost > 0) & (d.unit_cost < 1_000_000))),
            ("own_price_out_of_range", lambda d: _ok((d.own_price > 0) | d.own_price.isna()) if "own_price" in d.columns
             else pd.Series(True, index=d.index)),
            ("bad_currency", lambda d: _ok(d.currency.str.fullmatch(r"[A-Za-z]{3}"))),
        ],
        normalize=lambda df: df.assign(currency=df["currency"].str.upper(), country=df["country"].str.upper()),
        strict_columns=True,
    ),
    # Tombstone: deletion request. record_key = the type's key columns joined with "|" (numbers: %g format, dates: YYYY-MM-DD)
    "tombstone": Contract(
        name="tombstone",
        key=["record_type", "record_key"],
        required=["record_type", "record_key"],
        dtypes={"record_type": "str", "record_key": "str", "deleted_at": "datetime"},
    ),
    "review": Contract(
        name="review",
        key=["review_id"],
        required=["review_id", "product_id", "review_text"],
        dtypes={"review_id": "str", "product_id": "str", "review_text": "str",
                "rating": "float", "created_at": "datetime"},
        checks=[
            ("review_too_short", lambda d: _ok(d.review_text.str.len() >= 3)),
            ("rating_out_of_range", lambda d: _ok(d.rating.between(1, 5) | d.rating.isna()) if "rating" in d.columns
             else pd.Series(True, index=d.index)),
        ],
    ),
}


def norm_col(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def classify(columns: List[str]) -> Optional[str]:
    """Record type of a file from its column list; None if no contract matches."""
    cols = {norm_col(c) for c in columns}
    matches = [c for c in CONTRACTS.values() if set(c.required) <= cols]
    if not matches:
        return None
    return max(matches, key=lambda c: len(c.required)).name   # most specific match


def clean(raw: pd.DataFrame, contract: Contract) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, int]]:
    """Returns (good rows, deduplicated; rejected rows with reasons; metrics)."""
    df = raw.copy()
    df.columns = [norm_col(c) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    dropped_cols = 0
    if contract.strict_columns:
        allowed = set(contract.dtypes) | set(contract.required) | set(contract.key)
        dropped_cols = int((~df.columns.isin(allowed)).sum())
        df = df.loc[:, df.columns.isin(allowed)]

    for col, kind in contract.dtypes.items():
        if col not in df.columns:
            continue
        if kind == "str":
            df[col] = df[col].astype("string").str.strip().replace("", pd.NA)
        elif kind in ("float", "int"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif kind == "datetime":
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    reason = pd.Series("", index=df.index, dtype=object)
    for col in contract.required:
        bad = df[col].isna() & (reason == "")
        reason[bad] = f"null_or_invalid:{col}"
    for name, fn in contract.checks:
        valid = fn(df)
        reason[(~valid) & (reason == "")] = name

    rejected = df[reason != ""].assign(reject_reason=reason[reason != ""])
    good = df[reason == ""].copy()
    for col, kind in contract.dtypes.items():
        if kind == "int" and col in good.columns:
            good[col] = good[col].astype(int)
    if contract.normalize:
        good = contract.normalize(good)

    before = len(good)
    good = good.drop_duplicates(subset=contract.key, keep="last")
    metrics = {"rows_in": len(df), "rejected": len(rejected), "duplicates_in_batch": before - len(good), "rows_good": len(good),
               "dropped_columns": dropped_cols}
    return good.reset_index(drop=True), rejected.reset_index(drop=True), metrics
