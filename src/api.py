"""REST API: packages the 3 tiers (AI -> ML -> Agent) into a service that can be integrated into real systems.

Run:  python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
Authentication (X-API-Key header): one key per user (python -m src.auth create), or the admin key API_KEY in the environment.
Nothing configured: dev mode (open) unless REQUIRE_API_KEY=1 (then 503, never silently open).
Behind a trusted reverse proxy, set TRUST_PROXY=1 to read the real client IP from X-Forwarded-For (last entry added by the proxy).
"""
import hmac
import ipaddress
import json
import logging
import logging.handlers
import os
import threading
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
RATE_LIMIT_PER_MIN = 600          # per IP (an office often shares one IP, so keep this high)
USER_RATE_LIMIT_PER_MIN = 120     # per key: one heavy user cannot starve the others
AUTH_FAIL_LIMIT_PER_MIN = 10      # more wrong keys than this and the IP is blocked temporarily (anti key-guessing)
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
            return str(ipaddress.ip_address(last))      # accept only valid IP addresses, never arbitrary strings
        except ValueError:
            pass
    return direct


@app.middleware("http")
async def guard(request: Request, call_next):
    """Limits body size, per-IP rate; adds security headers and an audit log (never logs content), even for server errors."""
    t0 = time.monotonic()
    rid = uuid.uuid4().hex
    who = client_ip(request)
    request.state.principal = None
    declared = request.headers.get("content-length")
    if declared is not None and not declared.isdigit():
        response = JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
    elif declared and int(declared) > MAX_BODY_BYTES:
        response = JSONResponse({"detail": "Request body too large"}, status_code=413)
    elif request.method in ("POST", "PUT", "PATCH") and declared is None:
        response = JSONResponse({"detail": "Content-Length required"}, status_code=411)   # a chunked body would bypass the size cap
    elif _over(_hits, who, RATE_LIMIT_PER_MIN):
        response = JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
    else:
        _hits[who].append(time.monotonic())
        try:
            response = await call_next(request)
        except Exception:                      # unhandled error: still send security headers and write an audit record
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
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
        return Principal("dev", "dev", "admin")                   # local dev mode
    token = x_api_key or ""
    if env_key and hmac.compare_digest(token.encode(), env_key.encode()):
        return Principal("env", "env-admin", "admin")
    principal = auth.authenticate(token)
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return principal


def require_role(role: str):
    def dep(request: Request, x_api_key: Optional[str] = Header(default=None)) -> Principal:
        try:
            principal = authenticate_request(x_api_key)
        except HTTPException as exc:
            # Too many wrong keys from one IP: throttle further WRONG keys only. Valid keys always pass, so an attacker
            # sharing an IP (office, reverse proxy) cannot lock out legitimate users.
            if exc.status_code == 401 and _over(_fails, client_ip(request), AUTH_FAIL_LIMIT_PER_MIN):
                raise HTTPException(status_code=429, detail="Too many failed authentication attempts",
                                    headers={"Retry-After": "60"})
            raise
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
    lang: str = Field(default="en", pattern="^(en|fr)$")   # language of the executive summary

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


_cache_lock = threading.Lock()


def analyze_cached(text: str) -> Dict[str, object]:
    """Each distinct review is analysed only once (saves tokens when the LLM is on). Thread-safe."""
    with _cache_lock:
        hit = _review_cache.get(text)
    if hit is None:
        hit = state["extractor"].analyze_single_review(text)  # type: ignore[union-attr]
        with _cache_lock:
            if len(_review_cache) >= _CACHE_MAX:
                _review_cache.pop(next(iter(_review_cache)), None)
            _review_cache[text] = hit
    return dict(hit)


@app.post("/v1/review/analyze", dependencies=[Depends(require_key)])
def analyze_review(body: ReviewIn) -> Dict[str, object]:
    return analyze_cached(body.text)


@app.post("/v1/admin/reload", dependencies=[Depends(require_admin)])
def reload_model() -> Dict[str, object]:
    """Reload the model after the pipeline retrains, without restarting the service. Admin role only."""
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
        top_complaints=complaints, current_sentiment=float(sig["sentiment"]), lang=body.lang,
    )
    return {"signals": sig, "decision": decision, "model_version": state.get("model_version")}
