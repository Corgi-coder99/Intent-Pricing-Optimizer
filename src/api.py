"""REST API: đóng gói 3 tầng (AI -> ML -> Agent) thành dịch vụ có thể tích hợp vào hệ thống thật.

Chạy:  uvicorn src.api:app --host 127.0.0.1 --port 8000
Bảo mật: đặt biến môi trường API_KEY để bắt buộc header `X-API-Key`. Không đặt = chế độ dev (mở).
"""
import hmac
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from src.agent_decision import PricingDecisionAgent
from src.ai_extractor import ReviewSignalExtractor
from src.config import MODEL_ARTIFACT_PATH
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
    yield
    state.clear()


app = FastAPI(title="Intent-Driven Pricing API", version="1.0.0", lifespan=lifespan)


MAX_BODY_BYTES = 64 * 1024
RATE_LIMIT_PER_MIN = 120          # mỗi client
AUTH_FAIL_LIMIT_PER_MIN = 10      # sai khoá quá số này thì bị chặn tạm (chống dò khoá)
_hits: Dict[str, deque] = defaultdict(deque)
_fails: Dict[str, deque] = defaultdict(deque)


def reset_limits() -> None:
    _hits.clear()
    _fails.clear()


def _over(bucket: Dict[str, deque], who: str, limit: int, window: float = 60.0) -> bool:
    now = time.monotonic()
    q = bucket[who]
    while q and now - q[0] > window:
        q.popleft()
    return len(q) >= limit


@app.middleware("http")
async def guard(request: Request, call_next):
    """Chống quá tải/dò khoá: giới hạn kích thước body, tốc độ gọi, số lần sai khoá; thêm header bảo mật.
    Lưu ý: sau reverse proxy, client.host là IP của proxy; hãy giới hạn tốc độ cả ở proxy."""
    who = request.client.host if request.client else "unknown"
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    if _over(_hits, who, RATE_LIMIT_PER_MIN) or _over(_fails, who, AUTH_FAIL_LIMIT_PER_MIN):
        return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
    _hits[who].append(time.monotonic())
    response = await call_next(request)
    if response.status_code == 401:
        _fails[who].append(time.monotonic())
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("API_KEY", "")
    if not expected and os.getenv("REQUIRE_API_KEY") == "1":
        raise HTTPException(status_code=503, detail="Server misconfigured: API_KEY is required")   # không mở công khai do quên cấu hình
    if expected and not hmac.compare_digest((x_api_key or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


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
    return {"status": "ok", "model_loaded": "model" in state}


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


@app.post("/v1/admin/reload", dependencies=[Depends(require_key)])
def reload_model() -> Dict[str, object]:
    """Nạp lại mô hình sau khi pipeline huấn luyện lại, không cần khởi động lại dịch vụ."""
    model = DynamicPricingModel()
    try:
        model.load()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Model not trained. Run: python -m src.train_pipeline")
    state["model"] = model
    return {"reloaded": True}


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
    return {"signals": sig, "curve": curve}


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
    return {"signals": sig, "decision": decision}
