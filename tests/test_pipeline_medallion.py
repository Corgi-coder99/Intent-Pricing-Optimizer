import os
import threading
import time

import pandas as pd
import pytest

from src.data_generator import generate_ecommerce_dataset
from src.pipeline import CycleError, Layout, PipelineBusy, run_pipeline
from src.pipeline.contracts import classify
from src.pipeline.infra import RunLock
from src.pipeline.orchestrator import Node, topological_levels


@pytest.fixture()
def layout(tmp_path):
    return Layout(tmp_path).ensure()


@pytest.fixture(scope="module")
def sessions():
    return generate_ecommerce_dataset(n_samples=400, save=False)


def put(layout, df, name):
    df.to_csv(layout.landing / name, index=False)


def silver(layout, t):
    return pd.read_parquet(layout.silver / f"{t}.parquet")


def test_classification_by_schema():
    assert classify(["session_id", "product_id", "category", "region", "base_price", "competitor_price", "offered_price",
                     "session_views", "cart_additions_7d", "recent_review_text", "converted"]) == "session"
    assert classify(["Product Code", "PRICE", "Currency", "Country", "Observed At"]) == "price_observation"
    assert classify(["review_id", "product_id", "review_text"]) == "review"
    assert classify(["foo", "bar"]) is None


def test_end_to_end_and_idempotent_rerun(layout, sessions):
    put(layout, sessions, "s1.csv")
    r1 = run_pipeline(layout)
    assert r1["ok"] and r1["results"]["silver_session"]["rows_total"] == 400
    r2 = run_pipeline(layout)
    assert r2["results"]["bronze_ingest"]["duplicate_files_skipped"] == 1
    assert r2["results"]["silver_session"]["batches"] == 0
    assert r2["status"]["gold_sessions"] == "unchanged"      # đầu vào không đổi -> bỏ qua
    assert len(silver(layout, "session")) == 400


def test_same_content_other_filename_is_not_duplicated(layout, sessions):
    put(layout, sessions, "a.csv")
    put(layout, sessions, "copy_of_a.csv")
    run_pipeline(layout)
    assert len(silver(layout, "session")) == 400
    assert len(list((layout.bronze / "session").iterdir())) == 1


def test_overlapping_batches_are_upserted(layout, sessions):
    put(layout, sessions.iloc[:300], "b1.csv")
    put(layout, sessions.iloc[200:], "b2.csv")           # 100 dòng trùng session_id
    r = run_pipeline(layout)
    s = silver(layout, "session")
    assert len(s) == 400 and s["session_id"].is_unique
    assert r["results"]["silver_session"]["duplicates_vs_existing"] == 100


def test_bad_rows_quarantined_with_reason(layout, sessions):
    bad = sessions.iloc[:10].copy()
    bad.loc[bad.index[0], "offered_price"] = -5
    bad.loc[bad.index[1], "converted"] = 7
    bad.loc[bad.index[2], "region"] = None
    put(layout, bad, "bad.csv")
    r = run_pipeline(layout)
    assert r["results"]["silver_session"]["rejected"] == 3
    assert len(silver(layout, "session")) == 7
    q = pd.concat(pd.read_parquet(p) for p in (layout.quarantine / "session").iterdir())
    assert set(q["reject_reason"]) == {"price_not_positive", "converted_not_binary", "null_or_invalid:region"}


def test_mixed_types_routed_and_unknown_kept_aside(layout, sessions):
    put(layout, sessions.iloc[:50], "sessions.csv")
    pd.DataFrame({"product_code": ["1", "1", "2"], "price": [1.5, 1.5, 2.0], "currency": ["eur", "eur", "gbp"],
                  "country": ["fr", "fr", "gb"], "observed_at": ["2026-01-01"] * 3, "store": ["A", "A", "B"]}
                 ).to_csv(layout.landing / "prices.csv", index=False)
    pd.DataFrame({"x": [1], "y": [2]}).to_csv(layout.landing / "weird.csv", index=False)
    r = run_pipeline(layout)
    assert r["ok"] and r["results"]["bronze_ingest"]["unclassified"] == 1
    p = silver(layout, "price_observation")
    assert len(p) == 2 and set(p["currency"]) == {"EUR", "GBP"}      # trùng bị khử, tiền tệ chuẩn hoá
    assert (layout.bronze / "_unclassified").exists()
    assert (layout.gold / "competitor_prices.parquet").exists()


def test_cycle_and_missing_dependency_detected():
    f = lambda c: {}
    with pytest.raises(CycleError):
        topological_levels({"a": Node("a", f, ("b",)), "b": Node("b", f, ("a",))})
    with pytest.raises(CycleError):
        topological_levels({"a": Node("a", f, ("ghost",))})
    levels = topological_levels({"a": Node("a", f), "b": Node("b", f, ("a",)), "c": Node("c", f, ("a",))})
    assert levels == [["a"], ["b", "c"]]


def test_cycle_rejected_before_touching_data(layout):
    f = lambda c: {}
    with pytest.raises(CycleError):
        run_pipeline(layout, graph={"a": Node("a", f, ("b",)), "b": Node("b", f, ("a",))})
    assert not (layout.state / "last_run.json").exists()


def test_failed_node_skips_dependents_without_retry(layout):
    calls = {"n": 0}

    def boom(c):
        calls["n"] += 1
        raise RuntimeError("x")

    g = {"a": Node("a", boom), "b": Node("b", lambda c: {}, ("a",)), "c": Node("c", lambda c: {})}
    r = run_pipeline(layout, graph=g)
    assert r["status"] == {"a": "failed", "b": "skipped_dependency", "c": "ok"} and calls["n"] == 1 and not r["ok"]
    assert not (layout.state / "pipeline.lock").exists()           # khoá luôn được nhả


def test_lock_blocks_second_runner_and_releases(layout):
    a = RunLock(layout.state / "l.lock")
    b = RunLock(layout.state / "l.lock")
    a.acquire()
    with pytest.raises(PipelineBusy):
        b.acquire()
    b.release()                                                     # không được xoá khoá của người khác
    assert (layout.state / "l.lock").exists()
    a.release()
    b.acquire()
    b.release()


def test_stale_lock_is_reclaimed(layout):
    p = layout.state / "s.lock"
    p.write_text("{}")
    old = time.time() - 10_000
    os.utime(p, (old, old))
    lk = RunLock(p, ttl=60)
    lk.acquire()
    lk.release()


def test_parallel_runs_never_corrupt_or_duplicate(layout, sessions):
    put(layout, sessions, "p.csv")
    outcomes = []

    def worker():
        try:
            outcomes.append(run_pipeline(layout, wait_lock=60)["ok"])
        except Exception as exc:     # noqa
            outcomes.append(repr(exc))

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert outcomes == [True] * 4
    s = silver(layout, "session")
    assert len(s) == 400 and s["session_id"].is_unique
    assert len(list((layout.bronze / "session").iterdir())) == 1
    assert not list(layout.silver.glob("*.tmp"))


def test_parallel_fail_fast_without_wait(layout, sessions):
    put(layout, sessions, "p.csv")
    lock = RunLock(layout.state / "pipeline.lock")
    lock.acquire()
    try:
        with pytest.raises(PipelineBusy):
            run_pipeline(layout, wait_lock=0)
    finally:
        lock.release()
