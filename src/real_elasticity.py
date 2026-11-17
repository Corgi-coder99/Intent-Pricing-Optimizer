"""Ước lượng độ co giãn của cầu theo giá từ giao dịch THẬT (Online Retail II, UCI, CC BY 4.0) để kiểm tra các giả định
của bộ mô phỏng trước khi dùng vào mô hình.

Dữ liệu: sản phẩm x tuần (số lượng, giá bình quân gia quyền theo số lượng) từ bảng `clean_lines` của dự án
online-retail-insights (chỉ đọc). Phương pháp: log q = a_i + g_t + e * log p + u với hiệu ứng cố định theo sản phẩm (a_i) và tuần (g_t),
ước lượng bằng Frisch-Waugh (khử trung bình luân phiên); khoảng tin cậy 95% bằng bootstrap theo sản phẩm.

GIỚI HẠN (quan trọng): giá thay đổi không ngẫu nhiên (khuyến mãi trùng với mùa/nhu cầu, giá bán sỉ theo bậc số lượng) nên
đây là độ co giãn QUAN SÁT được, không phải nhân quả; hãy đọc như một khoảng hợp lý, không phải con số chính xác.
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

DEFAULT_DB = Path(os.getenv("ONLINE_RETAIL_DB", r"D:\Dev\online-retail-insights\data\warehouse.duckdb"))
EU_OTHER = ("Austria", "Belgium", "Cyprus", "Czech Republic", "Denmark", "EIRE", "Finland", "France", "Germany", "Greece",
            "Italy", "Lithuania", "Malta", "Netherlands", "Poland", "Portugal", "Spain", "Sweden")


def load_weekly(db_path: Path = DEFAULT_DB, group: str = "all") -> pd.DataFrame:
    """Sản phẩm x tuần. group: all | uk | eu (các nước EU ngoài Anh). Mở cơ sở dữ liệu ở chế độ CHỈ ĐỌC."""
    import duckdb
    where = {"all": "1=1", "uk": "country = 'United Kingdom'",
             "eu": "country IN (" + ",".join(f"'{c}'" for c in EU_OTHER) + ")"}[group]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(f"""
            SELECT stock_code AS product, date_trunc('week', invoice_date) AS week,
                   SUM(quantity)::DOUBLE AS units, SUM(quantity * price) / SUM(quantity) AS price,
                   COUNT(DISTINCT invoice) AS invoices
            FROM clean_lines
            WHERE line_type = 'sale' AND quantity > 0 AND price > 0 AND {where}
            GROUP BY 1, 2""").df()
    finally:
        con.close()


def quality_gate(df: pd.DataFrame, min_weeks: int = 30, min_price_cv: float = 0.05, min_distinct_prices: int = 3,
                 min_invoices: int = 2) -> pd.DataFrame:
    """Chỉ giữ sản phẩm đủ quan sát và CÓ biến động giá (không có biến động thì không ước lượng được độ co giãn)."""
    d = df[df["invoices"] >= min_invoices].copy()                          # tuần chỉ có 1 hoá đơn: quá nhiễu
    med = d.groupby("product")["price"].transform("median")
    d = d[(d["price"] >= 0.2 * med) & (d["price"] <= 5.0 * med)]            # loại giá nhập sai (cực đoan so với trung vị)
    g = d.groupby("product")["price"]
    stats = pd.DataFrame({"weeks": g.size(), "cv": g.std() / g.mean(), "distinct": g.nunique()})
    keep = stats[(stats["weeks"] >= min_weeks) & (stats["cv"] >= min_price_cv) & (stats["distinct"] >= min_distinct_prices)].index
    return d[d["product"].isin(keep)].reset_index(drop=True)


def _demean_two_way(v: np.ndarray, prod: np.ndarray, week: np.ndarray, iters: int = 30) -> np.ndarray:
    x = v.astype(float).copy()
    for _ in range(iters):
        for key in (prod, week):
            s = pd.Series(x).groupby(key).transform("mean").to_numpy()
            x = x - s
    return x


def estimate_elasticity(df: pd.DataFrame, n_boot: int = 300, seed: int = 42) -> Dict[str, object]:
    """Độ co giãn chung (hiệu ứng cố định sản phẩm + tuần), khoảng tin cậy 95% bootstrap theo sản phẩm, và trung vị theo từng sản phẩm."""
    if df.empty:
        raise ValueError("No data after quality gate")
    prod = pd.factorize(df["product"])[0]
    week = pd.factorize(df["week"])[0]
    y, x = np.log(df["units"].to_numpy()), np.log(df["price"].to_numpy())
    yt, xt = _demean_two_way(y, prod, week), _demean_two_way(x, prod, week)
    eps = float((xt * yt).sum() / (xt * xt).sum())

    # bootstrap theo sản phẩm trên phần dư đã khử hiệu ứng (xấp xỉ, chấp nhận được cho khoảng tin cậy)
    n_p = prod.max() + 1
    num = np.bincount(prod, weights=xt * yt, minlength=n_p)
    den = np.bincount(prod, weights=xt * xt, minlength=n_p)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n_p, n_p)
        d = den[idx].sum()
        if d > 0:
            boots.append(num[idx].sum() / d)
    lo, hi = np.percentile(boots, [2.5, 97.5])

    per_product = []
    for _, g in df.groupby("product"):
        gx, gy = np.log(g["price"].to_numpy()), np.log(g["units"].to_numpy())
        vx = gx - gx.mean()
        if (vx ** 2).sum() > 0:
            per_product.append(float((vx * (gy - gy.mean())).sum() / (vx ** 2).sum()))
    pp = np.array(per_product)
    return {"elasticity": round(eps, 3), "ci95": [round(float(lo), 3), round(float(hi), 3)], "n_products": int(n_p),
            "n_rows": int(len(df)), "per_product_median": round(float(np.median(pp)), 3),
            "share_negative": round(float((pp < 0).mean()), 3)}


def simulator_implied_elasticity(contexts: Optional[Dict[str, dict]] = None) -> Dict[str, float]:
    """Độ co giãn của XÁC SUẤT CHUYỂN ĐỔI theo giá mà mô hình đã huấn luyện (dữ liệu mô phỏng) ngụ ý quanh giá gốc."""
    from src.ml_pricing_model import DynamicPricingModel
    m = DynamicPricingModel()
    m.load()
    base = {"base_price": 100.0, "competitor_price": 100.0, "session_views": 4.0, "cart_additions_7d": 25.0}
    contexts = contexts or {"khong_phan_nan(sentiment 0.95)": {"ai_sentiment_score": 0.95, "pain_point_severity": 0.05},
                            "trung_binh(sentiment 0.5)": {"ai_sentiment_score": 0.5, "pain_point_severity": 0.5},
                            "nhieu_phan_nan(sentiment 0.1)": {"ai_sentiment_score": 0.1, "pain_point_severity": 0.9}}
    out = {}
    for name, ctx in contexts.items():
        curve = m.simulate_price_curve({**base, **ctx})
        near = curve.iloc[5:10]                                  # quanh giá gốc (-8% .. +1%)
        lp, lc = np.log(near["offered_price"].to_numpy()), np.log(near["conversion_prob"].to_numpy())
        out[name] = round(float(np.polyfit(lp, lc, 1)[0]), 3)
    return out


def main(argv: Optional[list] = None) -> int:
    if not DEFAULT_DB.exists():
        print(f"ERROR: warehouse not found: {DEFAULT_DB} (đặt biến môi trường ONLINE_RETAIL_DB)", file=sys.stderr)
        return 2
    report: Dict[str, object] = {"source": "Online Retail II (UCI, CC BY 4.0), qua online-retail-insights/clean_lines"}
    for group in ("all", "uk", "eu"):
        raw = load_weekly(DEFAULT_DB, group)
        gated = quality_gate(raw)
        report[group] = {"weekly_rows_before_gate": len(raw), "products_before_gate": int(raw["product"].nunique()),
                         **(estimate_elasticity(gated) if len(gated) else {"error": "no data after gate"})}
    try:
        report["simulator_implied_conversion_elasticity"] = simulator_implied_elasticity()
    except FileNotFoundError:
        report["simulator_implied_conversion_elasticity"] = "model not trained"
    out = Path(__file__).resolve().parent.parent / "data" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "real_elasticity.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
