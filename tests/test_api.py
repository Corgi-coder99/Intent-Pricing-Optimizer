import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.config import MODEL_ARTIFACT_PATH
from src.train_pipeline import run_full_pipeline


@pytest.fixture(scope="module")
def client():
    if not MODEL_ARTIFACT_PATH.exists():
        run_full_pipeline()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["model_loaded"] is True


def test_recommend_defensive(client):
    r = client.post("/v1/price/recommend", json={
        "product_name": "Headphones", "base_price": 149.99, "competitor_price": 145.0,
        "review_text": "Battery drains much faster than advertised after just two weeks. Button feels loose and broken.",
        "session_views": 5, "cart_additions_7d": 20,
    })
    assert r.status_code == 200
    assert r.json()["decision"]["strategy_name"] == "Defensive Conversion Recovery"


def test_simulate_curve_bounds(client):
    r = client.post("/v1/price/simulate", json={"base_price": 100, "competitor_price": 100, "ai_sentiment_score": 0.55})
    curve = r.json()["curve"]
    assert len(curve) == 15 and all(0 <= p["conversion_prob"] <= 1 for p in curve)


@pytest.mark.parametrize("payload", [
    {"base_price": -5, "competitor_price": 10, "ai_sentiment_score": 0.5},
    {"base_price": 10, "competitor_price": 10},
    {"base_price": 10, "competitor_price": 10, "ai_sentiment_score": 1.5},
])
def test_validation_rejects_bad_input(client, payload):
    assert client.post("/v1/price/recommend", json=payload).status_code == 422


def test_api_key_enforced(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    body = {"text": "great"}
    assert client.post("/v1/review/analyze", json=body).status_code == 401
    assert client.post("/v1/review/analyze", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/v1/review/analyze", json=body, headers={"X-API-Key": "secret"}).status_code == 200
