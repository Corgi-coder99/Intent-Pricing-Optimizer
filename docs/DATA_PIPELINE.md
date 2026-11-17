# Data pipeline: bronze -> silver -> gold

```
landing/ (source files dropped here, never modified)
   -> bronze/<type>/<hash>_<name>   immutable copy, classified by schema, same-hash files skipped
   -> silver/<type>.parquet         typed + validated + deduplicated by key; bad rows -> quarantine/<type>/
   -> gold/*.parquet                sessions_features, region_summary, category_summary, competitor_prices, market_margin
```
Run: `python -m src.pipeline run [--simulate N] [--wait SEC]`; status: `python -m src.pipeline status`;
validate a file without ingesting it: `python -m src.pipeline check --file <path> --type <contract>`.

## Record types (src/pipeline/contracts.py)
| Type | Detected by required columns | Dedup key |
|---|---|---|
| session | session_id, product_id, category, region, prices, behaviour, review, converted | session_id |
| price_observation | product_code, price, currency, country, observed_at | product_code + store + country + city + observed_at + price |
| review | review_id, product_id, review_text | review_id |
| fx_rate | rate_date, currency, eur_per_unit | rate_date + currency |
| branch_cost | product_code, country, unit_cost, currency (**strict columns**: anything else is dropped) | product_code + country |
| tombstone | record_type, record_key | (deletion request) |

A file that matches no contract goes to `bronze/_unclassified` and never reaches silver. Adding a source = adding a contract.

## Keeping data clean
- Bronze files are named by SHA-256: identical content (even renamed) is accepted once.
- Silver upserts by key (latest wins), so overlapping batches never create duplicate rows.
- Rows with wrong types or out-of-range values go to quarantine with a `reject_reason`; they are never silently deleted.
- Every file is written as tmp + `os.replace` (atomic); concurrent readers never see a half-written file.

## Preventing loops and clashes when running in parallel
- The DAG is checked with Kahn's algorithm before any data is touched; a cycle or missing dependency raises `CycleError`.
- Each step runs at most once per run; a failed or timed-out step is not retried and its dependents are skipped.
- Independent steps (the silver steps, some gold steps) run in parallel in a thread pool.
- A pipeline-wide lock (`state/pipeline.lock`, created with O_EXCL): a second run waits (`--wait`) or exits with code 2.
- The lock owner sends a heartbeat every 5 s; the 600 s TTL only reclaims locks of dead processes.
- Gold steps are skipped when the fingerprint of their inputs (including the extractor source code) has not changed.

## Per-step timeouts (enforced)
Every step runs in a child process; past its timeout it is killed, its dependents are skipped and the lock is released.
Writes are atomic, so a kill cannot corrupt data; orphaned `.tmp` files are cleaned on the next run.

Measured on 50,000 rows: every step < 2 s. Recommended timeouts (about 50-100x the measured time):
| Step | Timeout |
|---|---|
| generate_simulated, bronze_ingest, gold summaries, gold competitor prices, market margin | 120 s |
| silver_* | 300 s |
| gold sessions (offline) | 300 s |
| gold sessions (LLM) | 1800 s |
Data 20x larger still fits. Edit `build_nodes()` in `src/pipeline/orchestrator.py`.
Cost: each child process takes ~2 s to start on Windows, so the whole pipeline takes ~10 s instead of ~2.5 s.
Use `--no-isolate` for speed (timeouts are then not enforced).

## Old data is NOT deleted
- Bronze is immutable and append-only. Silver only processes new files (incremental), never rebuilds from scratch.
- Updated rows: the old version is stored in `silver/history/<type>.parquet` with `superseded_at` and `superseded_by_batch`.
- Analysed reviews are cached durably (`state/review_signal_cache.json`); the cache is invalidated automatically when the
  extractor keywords or logic code change.
- Retraining happens only when the gold data or the model code changed.
- What is recomputed when data changes: the gold tables (seconds) and the model (seconds).

## Deleting data (tombstones) and bronze cleanup
- To delete records, drop a CSV with columns `record_type` (session | price_observation | review) and `record_key`
  (the key columns joined with `|`; numbers as `%g`, dates as `YYYY-MM-DD`; e.g. `1|A|FR|Paris|2026-01-01|1.5`).
  Silver removes the matching rows and saves them to `silver/history/` with `history_reason=deleted`. Applied in ingestion order.
- Bronze: processed files are deleted after `bronze_retention_days` (90) or when over `bronze_max_bytes` (2 GB, oldest first).
  The manifest keeps the hash, so re-dropping the same content is not counted twice. Unprocessed files are never deleted.
- Quarantine is kept 30 days, history 365 days (all configurable in `src/pipeline/policy.py`).

## Overload protection (policy.py)
| Limit | Default | When exceeded |
|---|---|---|
| Size of one file | 500 MB | rejected, recorded once, not copied |
| New files per run | 200 | the rest wait for the next run |
| Chunked reading | 200,000 rows | RAM does not grow with file size |
| Quarantine rows per batch | 100,000 | truncated, `quarantine_truncated` flag |
| Minimum free disk | 1 GB | refuses to run (`InsufficientResources`) |
| File still being written | 0.5 s stable | deferred to the next run |
Tip for sources writing large files: write to `*.tmp`, then rename to `.csv`.

## Remaining limits
- Silver and gold use in-memory pandas; the practical ceiling is a few million rows. Beyond that, move to DuckDB/Spark.
- Application order follows ingestion order; within one run, files are sorted by name.
- There is no timeout per chunk inside a step (only per whole step).
- A new simulation (different content) accumulates into silver by session_id; delete `data/` to start from scratch.
