import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src import api, auth
from src.config import MODEL_ARTIFACT_PATH
from src.ml_pricing_model import DynamicPricingModel
from src.pipeline.nodes import robust_price_stats
from src.train_pipeline import run_full_pipeline


@pytest.fixture(scope="module")
def client():
    if not MODEL_ARTIFACT_PATH.exists():
        run_full_pipeline()
    with TestClient(app=api.app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "KEY_FILE", tmp_path / "keys.json")
    monkeypatch.setenv("AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    api._audit.handlers.clear()
    api.reset_limits()
    yield tmp_path
    api._audit.handlers.clear()
    api.reset_limits()


BODY = {"base_price": 100, "competitor_price": 100, "ai_sentiment_score": 0.6}


# ---------------------------------------------------------------- model: no more pickle
def test_model_saved_in_native_text_format_not_pickle():
    head = MODEL_ARTIFACT_PATH.read_bytes()[:64]
    assert MODEL_ARTIFACT_PATH.suffix == ".txt" and head.startswith(b"tree")      # LightGBM text, not pickle
    assert not (b"\x80\x04" in head or b"cposix" in head)


def test_same_model_file_gives_identical_predictions():
    ctx = {"base_price": 100.0, "competitor_price": 95.0, "ai_sentiment_score": 0.5, "pain_point_severity": 0.4,
           "session_views": 4.0, "cart_additions_7d": 25.0}
    a, b = DynamicPricingModel(), DynamicPricingModel()
    a.load()
    b.load()
    pd.testing.assert_frame_equal(a.simulate_price_curve(ctx), b.simulate_price_curve(ctx))
    assert a.version == b.version != "none"


# ---------------------------------------------------------------- per-user accounts
def test_per_user_keys_roles_and_no_plaintext_storage(client, isolated):
    alice = auth.create_key("alice", "user")
    boss = auth.create_key("boss", "admin")
    stored = (isolated / "keys.json").read_text()
    assert alice.split(".")[1] not in stored and boss.split(".")[1] not in stored      # only the hash is stored
    assert client.post("/v1/price/recommend", json=BODY, headers={"X-API-Key": alice}).status_code == 200
    assert client.post("/v1/admin/reload", headers={"X-API-Key": alice}).status_code == 403
    assert client.post("/v1/admin/reload", headers={"X-API-Key": boss}).status_code == 200
    assert client.post("/v1/price/recommend", json=BODY).status_code == 401
    assert client.post("/v1/price/recommend", json=BODY, headers={"X-API-Key": "abcd1234.wrong"}).status_code == 401


def test_two_users_get_identical_results_and_independent_rate_limits(client, monkeypatch):
    a, b = auth.create_key("a"), auth.create_key("b")
    monkeypatch.setattr(api, "USER_RATE_LIMIT_PER_MIN", 2)
    ra = [client.post("/v1/price/recommend", json=BODY, headers={"X-API-Key": a}) for _ in range(3)]
    assert [r.status_code for r in ra] == [200, 200, 429]                     # a is rate-limited...
    rb = client.post("/v1/price/recommend", json=BODY, headers={"X-API-Key": b})
    assert rb.status_code == 200                                              # ...b can still work normally
    assert rb.json()["decision"] == ra[0].json()["decision"]                  # same input + same model = same result
    assert rb.json()["model_version"] == ra[0].json()["model_version"]


def test_disabled_key_is_rejected(client):
    k = auth.create_key("temp")
    assert client.post("/v1/price/recommend", json=BODY, headers={"X-API-Key": k}).status_code == 200
    auth.disable_key(k.split(".")[0])
    assert client.post("/v1/price/recommend", json=BODY, headers={"X-API-Key": k}).status_code == 401


def test_key_validation_and_required_flag(client, monkeypatch):
    with pytest.raises(ValueError):
        auth.create_key("bad name!", "user")
    with pytest.raises(ValueError):
        auth.create_key("ok", "root")
    monkeypatch.setenv("REQUIRE_API_KEY", "1")
    assert client.post("/v1/price/recommend", json=BODY).status_code == 503


# ---------------------------------------------------------------- audit log + proxy
def test_audit_log_records_who_and_what_but_not_content(client, isolated):
    k = auth.create_key("alice")
    secret_text = "my private review text zzz"
    client.post("/v1/review/analyze", json={"text": secret_text}, headers={"X-API-Key": k})
    [h.flush() for h in api._audit.handlers]
    lines = [json.loads(x) for x in (isolated / "audit.log").read_text().splitlines()]
    rec = lines[-1]
    assert rec["key_id"] == k.split(".")[0] and rec["path"] == "/v1/review/analyze" and rec["status"] == 200
    assert "request_id" in rec and secret_text not in json.dumps(lines)


def test_forwarded_for_ignored_unless_proxy_trusted(client, monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MIN", 2)
    spoof = lambda ip: {"X-Forwarded-For": ip}
    codes = [client.get("/health", headers=spoof(f"10.0.0.{i}")).status_code for i in range(4)]
    assert codes == [200, 200, 429, 429]                                        # spoofing the header does not evade the limit
    api.reset_limits()
    monkeypatch.setenv("TRUST_PROXY", "1")
    codes = [client.get("/health", headers=spoof(f"10.0.0.{i}")).status_code for i in range(4)]
    assert codes == [200] * 4                                                   # trusted proxy: each real IP gets its own quota
    assert client.get("/health", headers=spoof("not-an-ip")).status_code == 200  # odd strings are ignored, no error


# ---------------------------------------------------------------- dirty data
def test_poisoned_prices_are_excluded_from_market_stats():
    rows = [{"product_code": "1", "country": "FR", "currency": "EUR", "store": f"S{i}", "price": 2.0 + (i % 3) * 0.1,
             "observed_at": "2026-01-01"} for i in range(8)]
    rows.append({"product_code": "1", "country": "FR", "currency": "EUR", "store": "EVIL", "price": 9999.0,
                 "observed_at": "2026-01-01"})
    g = robust_price_stats(pd.DataFrame(rows)).iloc[0]
    assert g["n_outliers"] == 1 and g["max_price"] < 3 and g["median_price"] < 3 and g["confidence"] == "ok"


def test_thin_evidence_is_flagged_low_confidence():
    g = robust_price_stats(pd.DataFrame([{"product_code": "2", "country": "DE", "currency": "EUR", "store": "A",
                                          "price": 1.0, "observed_at": "2026-01-01"}])).iloc[0]
    assert g["confidence"] == "low" and g["n_outliers"] == 0
