"""REST API: đóng gói 3 tầng (AI -> ML -> Agent) thành dịch vụ có thể tích hợp vào hệ thống thật.

Chạy:  python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
Xác thực (header X-API-Key): mỗi người dùng một khoá riêng (python -m src.auth create), hoặc khoá quản trị API_KEY trong
biến môi trường. Chưa cấu hình gì: chế độ dev (mở) trừ khi REQUIRE_API_KEY=1 (khi đó trả 503, không bao giờ mở công khai).
Sau reverse proxy đáng tin cậy, đặt TRUST_PROXY=1 để lấy IP thật từ X-Forwarded-For (phần tử cuối do proxy thêm).
"""
import hmac
import ipaddress
import json
import logging
import logging.handlers
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from src import auth
from src.agent_decision import PricingDecisionAgent
from src.ai_extractor import ReviewSignalExtractor
from src.auth import ROLES, Principal
from src.config import DATA_DIR, MODEL_ARTIFACT_PATH
from src.ml_pricing_model import DynamicPricingModel

state: Dict[str, object] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["extractor"] = ReviewSignalExtractor(use_llm=False)
    state["agent"] = PricingDecisionAgent(use_llm=False)
    model = DynamicPricingModel()
    if MODEL_ARTIFACT_PATH.exists():
        model.load()
        state["model"] = model
        state["model_version"] = model.version
    yield
    state.clear()


app = FastAPI(title="Intent-Driven Pricing API", version="1.1.0", lifespan=lifespan)


MAX_BODY_BYTES = 64 * 1024
RATE_LIMIT_PER_MIN = 600          # theo IP (văn phòng dùng chung một IP nên đặt cao)
USER_RATE_LIMIT_PER_MIN = 120     # theo từng khoá: một người dùng không làm nghẽn người khác
AUTH_FAIL_LIMIT_PER_MIN = 10      # sai khoá quá số này thì IP bị chặn tạm (chống dò khoá)
_hits: Dict[str, deque] = defaultdict(deque)
_user_hits: Dict[str, deque] = defaultdict(deque)
_fails: Dict[str, deque] = defaultdict(deque)

_audit = logging.getLogger("pricing.audit")


def _audit_handler() -> None:
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in _audit.handlers):
        path = os.getenv("AUDIT_LOG") or str(DATA_DIR / "state" / "audit.log")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        _audit.addHandler(h)
        _audit.setLevel(logging.INFO)
        _audit.propagate = False


def reset_limits() -> None:
    _hits.clear()
    _user_hits.clear()
    _fails.clear()


def _over(bucket: Dict[str, deque], who: str, limit: int, window: float = 60.0) -> bool:
    now = time.monotonic()
    q = bucket[who]
    while q and now - q[0] > window:
        q.popleft()
    return len(q) >= limit


def client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    if os.getenv("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        last = fwd.split(",")[-1].strip() if fwd else ""
        try:
            return str(ipaddress.ip_address(last))      # chỉ nhận địa chỉ IP hợp lệ, không nhận chuỗi tuỳ ý
        except ValueError:
            pass
    return direct


@app.middleware("http")
async def guard(request: Request, call_next):
    """Giới hạn kích thước body, tốc độ theo IP, số lần sai khoá; header bảo mật; nhật ký kiểm toán (không ghi nội dung)."""
    t0 = time.monotonic()
    rid = uuid.uuid4().hex
    who = client_ip(request)
    request.state.principal = None
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        response = JSONResponse({"detail": "Request body too large"}, status_code=413)
    elif _over(_hits, who, RATE_LIMIT_PER_MIN) or _over(_fails, who, AUTH_FAIL_LIMIT_PER_MIN):
        response = JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
    else:
        _hits[who].append(time.monotonic())
        response = await call_next(request)
        if response.status_code == 401:
            _fails[who].append(time.monotonic())
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Request-ID"] = rid
    if request.url.path != "/health":
        _audit_handler()
        p = request.state.principal
        _audit.info(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "request_id": rid, "ip": who,
                                "method": request.method, "path": request.url.path, "status": response.status_code,
                                "ms": round((time.monotonic() - t0) * 1000), "key_id": p.key_id if p else None,
                                "role": p.role if p else None}))
    return response


def authenticate_request(x_api_key: Optional[str]) -> Principal:
    env_key = os.getenv("API_KEY", "")
    if not env_key and not auth.has_keys():
        if os.getenv("REQUIRE_API_KEY") == "1":
            raise HTTPException(status_code=503, detail="Server misconfigured: no API key configured")
        return Principal("dev", "dev", "admin")                   # chế độ dev cục bộ
    token = x_api_key or ""
    if env_key and hmac.compare_digest(token.encode(), env_key.encode()):
        return Principal("env", "env-admin", "admin")
    principal = auth.authenticate(token)
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return principal


def require_role(role: str):
    def dep(request: Request, x_api_key: Optional[str] = Header(default=None)) -> Principal:
        principal = authenticate_request(x_api_key)
        request.state.principal = principal
        if ROLES[principal.role] < ROLES[role]:
            raise HTTPException(status_code=403, detail="Insufficient role")
        if _over(_user_hits, principal.key_id, USER_RATE_LIMIT_PER_MIN):
            raise HTTPException(status_code=429, detail="Per-key rate limit exceeded", headers={"Retry-After": "60"})
        _user_hits[principal.key_id].append(time.monotonic())
        return principal
    return dep


require_key = require_role("user")
require_admin = require_role("admin")


def get_model() -> DynamicPricingModel:
    model = state.get("model")
    if model is None:
        raise HTTPException(status_code=503, detail="Model not trained. Run: python -m src.train_pipeline")
    return model  # type: ignore[return-value]


class ReviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class PriceIn(BaseModel):
    product_name: str = Field(default="product", max_length=200)
    base_price: float = Field(gt=0, le=1_000_000)
    competitor_price: float = Field(gt=0, le=1_000_000)
    review_text: Optional[str] = Field(default=None, max_length=2000)
    ai_sentiment_score: Optional[float] = Field(default=None, ge=0, le=1)
    pain_point_severity: Optional[float] = Field(default=None, ge=0, le=1)
    session_views: float = Field(default=4.0, ge=0, le=1000)
    cart_additions_7d: float = Field(default=25.0, ge=0, le=100000)

    @model_validator(mode="after")
    def need_signal(self):
        if self.review_text is None and self.ai_sentiment_score is None:
            raise ValueError("Provide review_text or ai_sentiment_score")
        return self


def _resolve(body: PriceIn) -> Dict[str, object]:
    sig: Dict[str, object] = {}
    if body.review_text:
        sig = analyze_cached(body.review_text)
    sentiment = body.ai_sentiment_score if body.ai_sentiment_score is not None else float(sig["ai_sentiment_score"])
    if body.pain_point_severity is not None:
        severity = body.pain_point_severity
    elif "pain_point_severity" in sig:
        severity = float(sig["pain_point_severity"])
    else:
        severity = round(max(0.05, 1.0 - sentiment), 3)
    return {"sentiment": sentiment, "severity": severity, "category": sig.get("complaint_category", "unknown")}


@app.get("/health")
def health() -> Dict[str, object]:
    return {"status": "ok", "model_loaded": "model" in state, "model_version": state.get("model_version", "none")}


_review_cache: Dict[str, Dict[str, object]] = {}
_CACHE_MAX = 5000


def analyze_cached(text: str) -> Dict[str, object]:
    """Mỗi review khác nhau chỉ phân tích một lần (tiết kiệm token khi bật LLM)."""
    if text not in _review_cache:
        if len(_review_cache) >= _CACHE_MAX:
            _review_cache.pop(next(iter(_review_cache)))
        _review_cache[text] = state["extractor"].analyze_single_review(text)  # type: ignore[union-attr]
    return dict(_review_cache[text])


@app.post("/v1/review/analyze", dependencies=[Depends(require_key)])
def analyze_review(body: ReviewIn) -> Dict[str, object]:
    return analyze_cached(body.text)


@app.post("/v1/admin/reload", dependencies=[Depends(require_admin)])
def reload_model() -> Dict[str, object]:
    """Nạp lại mô hình sau khi pipeline huấn luyện lại, không cần khởi động lại dịch vụ. Chỉ vai trò admin."""
    model = DynamicPricingModel()
    try:
        model.load()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Model not trained. Run: python -m src.train_pipeline")
    state["model"] = model
    state["model_version"] = model.version
    return {"reloaded": True, "model_version": model.version}


@app.post("/v1/price/simulate", dependencies=[Depends(require_key)])
def simulate(body: PriceIn) -> Dict[str, object]:
    model = get_model()
    sig = _resolve(body)
    ctx = {
        "base_price": body.base_price, "competitor_price": body.competitor_price,
        "ai_sentiment_score": sig["sentiment"], "pain_point_severity": sig["severity"],
        "session_views": body.session_views, "cart_additions_7d": body.cart_additions_7d,
    }
    curve: List[dict] = model.simulate_price_curve(ctx).to_dict(orient="records")
    return {"signals": sig, "curve": curve, "model_version": state.get("model_version")}


@app.post("/v1/price/recommend", dependencies=[Depends(require_key)])
def recommend(body: PriceIn) -> Dict[str, object]:
    model = get_model()
    sig = _resolve(body)
    ctx = {
        "base_price": body.base_price, "competitor_price": body.competitor_price,
        "ai_sentiment_score": sig["sentiment"], "pain_point_severity": sig["severity"],
        "session_views": body.session_views, "cart_additions_7d": body.cart_additions_7d,
    }
    sim = model.simulate_price_curve(ctx)
    complaints = [] if sig["category"] in ("none", "unknown") else [str(sig["category"])]
    decision = state["agent"].recommend_action(  # type: ignore[union-attr]
        product_name=body.product_name, base_price=body.base_price, sim_df=sim,
        top_complaints=complaints, current_sentiment=float(sig["sentiment"]),
    )
    return {"signals": sig, "decision": decision, "model_version": state.get("model_version")}
