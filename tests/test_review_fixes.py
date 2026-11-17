"""Regression tests for the defects found by the independent code review (T-053) and the browser QA (T-052)."""
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src import api, auth
from src.ai_extractor import ReviewSignalExtractor
from src.config import MODEL_ARTIFACT_PATH
from src.market_view import comparable_products, country_summary
from src.pipeline import Layout, Policy, run_pipeline
from src.pipeline.infra import RunLock
from src.pipeline.nodes import _load_state, _norm_part
from src.rag_engine import ReviewRAGEngine
from src.train_pipeline import run_full_pipeline

ROOT = Path(__file__).resolve().parent.parent
EXT = ReviewSignalExtractor(use_llm=False)


# ---------------------------------------------------------------- extractor: whole-word matching
@pytest.mark.parametrize("text", ["Great chocolate, delicious breakfast", "A lovely porcelain plate"])
def test_substrings_inside_other_words_do_not_trigger_complaints(text):
    assert EXT.analyze_single_review(text)["complaint_category"] == "none"


def test_faster_is_not_a_positive_keyword_and_drains_fast_is_negative():
    out = EXT.analyze_single_review("Battery drains much faster than advertised after just two weeks.")
    assert out["ai_sentiment_score"] < 0.45                                  # was 0.46 because "fast" matched inside "faster"
    assert EXT.analyze_single_review("The battery drains fast")["ai_sentiment_score"] < 0.45


# ---------------------------------------------------------------- tombstone keys keep full float precision
def test_tombstone_key_precision_distinguishes_close_prices():
    assert _norm_part("123.4567", "float") != _norm_part("123.4568", "float")
    assert _norm_part("1.50", "float") == _norm_part("1.5", "float")


def test_tombstone_deletes_only_the_exact_price(tmp_path):
    layout = Layout(tmp_path).ensure()
    rows = [{"product_code": "1", "price": p, "currency": "EUR", "country": "FR", "store": "A", "city": "X", "observed_at": "2026-01-01"}
            for p in (123.4567, 123.4568)]
    pd.DataFrame(rows).to_csv(layout.landing / "a_prices.csv", index=False)
    run_pipeline(layout, isolate=False, policy=Policy())
    pd.DataFrame({"record_type": ["price_observation"], "record_key": ["1|A|FR|X|2026-01-01|123.4567"]}).to_csv(layout.landing / "b_tomb.csv", index=False)
    run_pipeline(layout, isolate=False, policy=Policy())
    left = pd.read_parquet(layout.silver / "price_observation.parquet")
    assert list(left["price"]) == [123.4568]


# ---------------------------------------------------------------- one atomic silver state file
def test_silver_state_is_one_file_and_legacy_two_file_layout_is_still_read(tmp_path):
    layout = Layout(tmp_path).ensure()
    (layout.state / "silver_session_processed.json").write_text('["abc"]')
    (layout.state / "silver_session_tombstones_processed.json").write_text('["def"]')
    assert _load_state(layout, "session") == (["abc"], ["def"])              # migration path
    pd.DataFrame([{"product_code": "1", "price": 1.0, "currency": "EUR", "country": "FR", "observed_at": "2026-01-01"}]).to_csv(
        layout.landing / "p.csv", index=False)
    run_pipeline(layout, isolate=False, policy=Policy())
    assert (layout.state / "silver_price_observation_state.json").exists()


# ---------------------------------------------------------------- OS-level run lock
def test_lock_is_released_automatically_when_the_owner_process_dies(tmp_path):
    path = tmp_path / "x.lock"
    code = ("import sys,time;from pathlib import Path;from src.pipeline.infra import RunLock;"
            "RunLock(Path(sys.argv[1])).acquire();print('locked',flush=True);time.sleep(120)")
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        assert RunLock.is_free(path) is False                                   # held by another process
        with pytest.raises(Exception):
            RunLock(path).acquire()
    finally:
        child.kill()
        child.wait()
    deadline = time.time() + 5
    while time.time() < deadline and not RunLock.is_free(path):                 # no TTL, no stale-lock race: the OS frees it
        time.sleep(0.1)
    assert RunLock.is_free(path)


def test_release_without_acquire_never_touches_another_holders_lock(tmp_path):
    a, b = RunLock(tmp_path / "y.lock"), RunLock(tmp_path / "y.lock")
    a.acquire()
    b.release()
    assert RunLock.is_free(tmp_path / "y.lock") is False
    a.release()
    assert RunLock.is_free(tmp_path / "y.lock")


# ---------------------------------------------------------------- RAG with a region filter (browser QA #1)
def test_rag_with_region_filter_does_not_crash_and_filters():
    df = pd.DataFrame({
        "product_id": ["P1"] * 4, "region": ["Paris", "Paris", "Lyon", "Lyon"],
        "recent_review_text": ["too expensive", "late box", "broken button", "great value"],
        "complaint_category": ["price_value", "shipping_packaging", "quality_defect", "none"],
        "ai_sentiment_score": [0.2, 0.3, 0.1, 0.9]})
    rag = ReviewRAGEngine(df)
    out = rag.retrieve_product_evidence("P1", region="Lyon")
    assert {q["review_text"] for q in out["evidence_quotes"]} == {"broken button", "great value"}
    assert rag.retrieve_product_evidence("P1", region="Nowhere")["evidence_quotes"] == []


# ---------------------------------------------------------------- country page consistency (browser QA #2)
def _row(code, country, conf="ok", vs=5.0):
    return {"product_code": code, "country": country, "market_net_eur": 1.0, "vs_home_pct": vs, "scenario_margin_pct": 20.0,
            "sector_gross_margin_pct": 22.0, "n_obs": 5, "n_stores": 3, "confidence": conf, "status": "ok", "home_country": "FR"}


def test_reliable_filter_requires_both_home_and_compared_rows_to_be_reliable():
    m = pd.DataFrame([_row("A", "FR", "ok", 0), _row("A", "DE", "ok"),
                      _row("B", "FR", "low", 0), _row("B", "DE", "ok")])            # B: reliable abroad but not at home
    assert country_summary(m, only_confident=True)["n_products"].tolist() == [1]
    assert country_summary(m)["n_products"].tolist() == [2]


def test_comparable_products_follow_the_country_selection():
    m = pd.DataFrame([_row("A", "FR", vs=0), _row("A", "DE"), _row("B", "FR", vs=0), _row("B", "IT"), _row("C", "FR", vs=0)])
    assert comparable_products(m) == ["A", "B"]
    assert comparable_products(m, countries=["DE"]) == ["A"]
    assert comparable_products(m, countries=[]) == []


# ---------------------------------------------------------------- API hardening from the review
@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "KEY_FILE", tmp_path / "keys.json")
    monkeypatch.setenv("AUDIT_LOG", str(tmp_path / "audit.log"))
    for var in ("API_KEY", "REQUIRE_API_KEY", "TRUST_PROXY"):
        monkeypatch.delenv(var, raising=False)
    api._audit.handlers.clear()
    api.reset_limits()
    if not MODEL_ARTIFACT_PATH.exists():
        run_full_pipeline()
    yield tmp_path
    api._audit.handlers.clear()
    api.reset_limits()


def test_chunked_body_without_content_length_is_refused(api_env):
    def chunks():
        yield b'{"text": "'
        yield b"x" * 10
        yield b'"}'

    with TestClient(api.app) as c:
        r = c.post("/v1/review/analyze", content=chunks(), headers={"content-type": "application/json"})
        assert r.status_code == 411


def test_deleting_the_key_file_does_not_reopen_the_api(api_env):
    auth.create_key("alice")
    auth.KEY_FILE.unlink()
    with TestClient(api.app) as c:
        assert c.post("/v1/review/analyze", json={"text": "great"}).status_code == 401     # not an open "dev" server


def test_unhandled_error_still_gets_security_headers_and_an_audit_record(api_env, monkeypatch):
    monkeypatch.setattr(api, "analyze_cached", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    with TestClient(api.app, raise_server_exceptions=False) as c:
        r = c.post("/v1/review/analyze", json={"text": "great"})
    assert r.status_code == 500 and r.headers["x-content-type-options"] == "nosniff" and "x-request-id" in r.headers
    [h.flush() for h in api._audit.handlers]
    assert '"status": 500' in (api_env / "audit.log").read_text()


def test_valid_key_still_works_after_too_many_wrong_keys_from_the_same_ip(api_env, monkeypatch):
    good = auth.create_key("alice")
    with TestClient(api.app) as c:
        for _ in range(api.AUTH_FAIL_LIMIT_PER_MIN):
            assert c.post("/v1/review/analyze", json={"text": "x"}, headers={"X-API-Key": "nope.nope"}).status_code == 401
        assert c.post("/v1/review/analyze", json={"text": "x"}, headers={"X-API-Key": "nope.nope"}).status_code == 429
        assert c.post("/v1/review/analyze", json={"text": "x"}, headers={"X-API-Key": good}).status_code == 200
        assert c.get("/health").status_code == 200


def test_kpi_count_matches_what_the_country_chart_compares():
    from src.market_view import compared_product_count
    m = pd.DataFrame([_row("A", "FR", vs=0), _row("A", "DE"), _row("A", "IT"),
                      _row("B", "DE", vs=None), _row("B", "IT", vs=None),                 # B has no home-country row, so no price gap
                      _row("C", "FR", "low", 0), _row("C", "DE", "ok")])
    assert compared_product_count(m) == 2                                                  # A and C (B cannot be compared with FR)
    assert compared_product_count(m, only_confident=True) == 1                             # C's home row is not reliable
    assert compared_product_count(m, countries=["IT"]) == 1
    assert country_summary(m)["n_products"].sum() == 3                                     # DE: A and C, IT: A (B is never compared)


def test_dashboard_runs_pipeline_in_process():
    """Streamlit scripts cannot be re-imported by spawned child processes (hosted first run hung), so the dashboard must pass isolate=False."""
    import inspect
    from pathlib import Path
    from src.train_pipeline import run_full_pipeline
    assert "isolate" in inspect.signature(run_full_pipeline).parameters
    assert "isolate=False" in (Path(__file__).resolve().parent.parent / "app" / "Dashboard.py").read_text(encoding="utf-8")
