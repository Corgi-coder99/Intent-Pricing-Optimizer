import threading
import time

import pandas as pd
import pytest

from src.data_generator import generate_ecommerce_dataset
from src.pipeline import InsufficientResources, Layout, Policy, run_pipeline
from src.pipeline.infra import read_json


@pytest.fixture()
def layout(tmp_path):
    return Layout(tmp_path).ensure()


@pytest.fixture(scope="module")
def sessions():
    return generate_ecommerce_dataset(n_samples=400, save=False)


def run(layout, **policy):
    return run_pipeline(layout, isolate=False, policy=Policy(**policy))


def bronze_files(layout):
    return [p for p in layout.bronze.rglob("*") if p.is_file()]


# ---------------------------------------------------------------- xoá theo tombstone
def test_tombstone_deletes_rows_keeps_history_and_is_idempotent(layout, sessions):
    sessions.to_csv(layout.landing / "a_data.csv", index=False)
    run(layout)
    ids = sessions["session_id"].iloc[:3].tolist()
    pd.DataFrame({"record_type": "session", "record_key": ids}).to_csv(layout.landing / "b_tomb.csv", index=False)
    r = run(layout)
    assert r["results"]["silver_session"]["deleted_rows"] == 3
    s = pd.read_parquet(layout.silver / "session.parquet")
    assert len(s) == 397 and not set(ids) & set(s["session_id"])
    h = pd.read_parquet(layout.silver / "history" / "session.parquet")
    assert set(h["history_reason"]) == {"deleted"} and set(h["session_id"]) == set(ids)
    run(layout)
    assert len(pd.read_parquet(layout.silver / "session.parquet")) == 397
    assert len(pd.read_parquet(layout.silver / "history" / "session.parquet")) == 3


def test_tombstone_matches_composite_key_with_number_and_date_normalisation(layout):
    pd.DataFrame({"product_code": ["1", "2"], "price": [1.5, 2.0], "currency": ["EUR", "EUR"], "country": ["FR", "FR"],
                  "store": ["A", "B"], "city": ["Paris", "Paris"], "observed_at": ["2026-01-01"] * 2}
                 ).to_csv(layout.landing / "p.csv", index=False)
    run(layout)
    pd.DataFrame({"record_type": ["price_observation"], "record_key": ["1|A|FR|Paris|2026-01-01|1.50"]}
                 ).to_csv(layout.landing / "t.csv", index=False)
    r = run(layout)
    assert r["results"]["silver_price_observation"]["deleted_rows"] == 1
    assert list(pd.read_parquet(layout.silver / "price_observation.parquet")["product_code"]) == ["2"]


# ---------------------------------------------------------------- lưu giữ / dọn bronze
def test_bronze_expired_after_retention_but_not_reingested(layout, sessions):
    sessions.to_csv(layout.landing / "a.csv", index=False)
    r = run(layout, bronze_retention_days=0)
    assert r["results"]["housekeeping"]["bronze_expired"] == 1 and not bronze_files(layout)
    assert len(pd.read_parquet(layout.silver / "session.parquet")) == 400       # silver còn nguyên
    sessions.to_csv(layout.landing / "same_content_new_name.csv", index=False)
    r2 = run(layout, bronze_retention_days=0)
    assert r2["results"]["bronze_ingest"]["new_files"] == 0                      # không bị nạp lại
    assert any(m["status"] == "expired" for m in read_json(layout.state / "bronze_manifest.json", {}).values())


def test_bronze_size_budget_expires_oldest_processed(layout, sessions):
    sessions.to_csv(layout.landing / "a.csv", index=False)
    r = run(layout, bronze_retention_days=9999, bronze_max_bytes=1)
    assert r["results"]["housekeeping"]["bronze_expired"] == 1


def test_unprocessed_bronze_is_never_expired(layout, sessions):
    sessions.to_csv(layout.landing / "a.csv", index=False)
    from src.pipeline.orchestrator import Node, build_nodes
    g = {k: v for k, v in build_nodes().items() if k in ("generate_simulated", "bronze_ingest", "housekeeping")}
    g["housekeeping"] = Node("housekeeping", g["housekeeping"].fn, ("bronze_ingest",))   # bỏ silver -> chưa xử lý
    run_pipeline(layout, graph=g, isolate=False, policy=Policy(bronze_retention_days=0, bronze_max_bytes=1))
    assert len(bronze_files(layout)) == 1


def test_history_and_quarantine_retention(layout, sessions):
    sessions.to_csv(layout.landing / "v1.csv", index=False)
    run(layout)
    ch = sessions.iloc[:4].copy()
    ch["offered_price"] += 5
    ch.to_csv(layout.landing / "v2.csv", index=False)
    r = run(layout, history_retention_days=0)
    assert r["results"]["housekeeping"]["history_rows_dropped"] == 4


# ---------------------------------------------------------------- chống quá tải
def test_oversized_file_rejected_not_copied(layout, sessions):
    sessions.to_csv(layout.landing / "big.csv", index=False)
    r = run(layout, max_file_bytes=100)
    assert r["ok"] and r["results"]["bronze_ingest"]["rejected_too_large"] == 1
    assert not bronze_files(layout) and not (layout.silver / "session.parquet").exists()
    assert run(layout, max_file_bytes=100)["results"]["bronze_ingest"]["rejected_too_large"] == 0   # ghi nhận một lần


def test_max_files_per_run_backpressure(layout, sessions):
    for i in range(3):
        sessions.iloc[i * 100:(i + 1) * 100].to_csv(layout.landing / f"f{i}.csv", index=False)
    r1 = run(layout, max_files_per_run=1)["results"]["bronze_ingest"]
    assert r1["new_files"] == 1 and r1["deferred_files"] == 2
    run(layout, max_files_per_run=1)
    run(layout, max_files_per_run=1)
    assert len(pd.read_parquet(layout.silver / "session.parquet")) == 300


def test_quarantine_is_capped(layout, sessions):
    bad = sessions.iloc[:10].copy()
    bad["offered_price"] = -1
    bad.to_csv(layout.landing / "bad.csv", index=False)
    r = run(layout, max_quarantine_rows_per_batch=2)["results"]["silver_session"]
    assert r["rejected"] == 10 and r["quarantine_truncated"] is True
    q = pd.concat(pd.read_parquet(p) for p in (layout.quarantine / "session").iterdir())
    assert len(q) == 2


def test_disk_guard_refuses_to_run(layout):
    with pytest.raises(InsufficientResources):
        run(layout, min_free_bytes=10 ** 18)
    assert not (layout.state / "pipeline.lock").exists()


def test_file_being_written_is_deferred_not_registered(layout, sessions):
    path = layout.landing / "growing.csv"
    sessions.iloc[:100].to_csv(path, index=False)

    def keep_writing():
        time.sleep(0.15)
        sessions.iloc[:200].to_csv(path, index=False)

    t = threading.Thread(target=keep_writing)
    t.start()
    r = run(layout, landing_settle_seconds=0.6)["results"]["bronze_ingest"]
    t.join()
    assert r["deferred_files"] == 1 and r["new_files"] == 0
    r2 = run(layout, landing_settle_seconds=0.6)["results"]["bronze_ingest"]       # ổn định -> nhận
    assert r2["new_files"] == 1


def test_chunked_reading_gives_same_result(layout, sessions):
    sessions.to_csv(layout.landing / "a.csv", index=False)
    r = run(layout, chunk_rows=37)["results"]["silver_session"]
    assert r["rows_total"] == 400 and r["rows_in"] == 400
