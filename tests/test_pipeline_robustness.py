import time

import pandas as pd
import pytest

from src.data_generator import generate_ecommerce_dataset
from src.pipeline import Layout, run_pipeline
from src.pipeline.orchestrator import Node
from tests import slow_nodes


@pytest.fixture()
def layout(tmp_path):
    return Layout(tmp_path).ensure()


@pytest.fixture(scope="module")
def sessions():
    return generate_ecommerce_dataset(n_samples=300, save=False)


def test_timeout_kills_slow_node_and_skips_dependents(layout):
    g = {"slow": Node("slow", slow_nodes.sleepy, timeout=2),
         "after": Node("after", slow_nodes.quick, ("slow",)),
         "other": Node("other", slow_nodes.quick)}
    t = time.time()
    r = run_pipeline(layout, graph=g, isolate=True)
    assert time.time() - t < 30                        # does not wait the full 60s of the hung step
    assert r["status"] == {"slow": "failed", "after": "skipped_dependency", "other": "ok"}
    assert r["results"]["slow"]["timed_out"] is True and "durations" in r
    assert not (layout.state / "pipeline.lock").exists()


def test_crashed_process_reported_not_hung(layout):
    r = run_pipeline(layout, graph={"c": Node("c", slow_nodes.crash, timeout=30)}, isolate=True)
    assert r["status"]["c"] == "failed" and "died" in r["results"]["c"]["error"]


def test_updated_rows_keep_history_and_rerun_is_stable(layout, sessions):
    sessions.to_csv(layout.landing / "v1.csv", index=False)
    run_pipeline(layout, isolate=False)
    changed = sessions.iloc[:5].copy()
    changed["offered_price"] = changed["offered_price"] + 10
    changed.to_csv(layout.landing / "v2.csv", index=False)
    r = run_pipeline(layout, isolate=False)
    assert r["results"]["silver_session"]["updated_rows"] == 5
    s = pd.read_parquet(layout.silver / "session.parquet").set_index("session_id")
    h = pd.read_parquet(layout.silver / "history" / "session.parquet").set_index("session_id")
    ids = changed["session_id"]
    assert len(s) == 300 and len(h) == 5
    assert (s.loc[ids, "offered_price"].values == changed["offered_price"].values).all()          # new version
    assert (h.loc[ids, "offered_price"].values == sessions.iloc[:5]["offered_price"].values).all()  # old version is still stored
    run_pipeline(layout, isolate=False)
    assert len(pd.read_parquet(layout.silver / "history" / "session.parquet")) == 5               # re-running does not duplicate


def test_review_signal_analyzed_once_across_runs(layout, sessions):
    sessions.iloc[:200].to_csv(layout.landing / "a.csv", index=False)
    r1 = run_pipeline(layout, isolate=False)["results"]["gold_sessions"]
    assert r1["reviews_analyzed_now"] > 0
    extra = sessions.iloc[200:].copy()
    extra["recent_review_text"] = "Brand new unseen sentence about a broken button."
    extra.to_csv(layout.landing / "b.csv", index=False)
    r2 = run_pipeline(layout, isolate=False)["results"]["gold_sessions"]
    assert r2["reviews_analyzed_now"] == 1 and r2["reviews_from_cache"] > 0
