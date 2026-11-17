"""CLI:  python -m src.pipeline run [--simulate N] [--wait SEC]   |   python -m src.pipeline status"""
import argparse
import json
import sys
from pathlib import Path

from src.config import DATA_DIR
from src.pipeline import Layout, PipelineBusy, run_pipeline


def check_file(path: Path, rtype: str) -> int:
    """Read-only report: no copy, no state written, never prints row contents."""
    import pandas as pd
    from src.pipeline.contracts import CONTRACTS, clean, norm_col
    from src.pipeline.nodes import iter_raw
    from src.pipeline.policy import DEFAULT_POLICY
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2
    contract = CONTRACTS[rtype]
    cols = [norm_col(c) for c in next(iter_raw(path, 5)).columns]
    missing = [c for c in contract.required if c not in cols]
    unknown = [c for c in cols if c not in set(contract.dtypes) | set(contract.required) | set(contract.key)]
    rows = good = rejected = dups = 0
    reasons: dict = {}
    extra_key = "columns_that_will_be_ignored" if contract.strict_columns else "extra_columns"
    if missing:                                    # missing required columns: report immediately, do not read the data
        print(json.dumps({"file": path.name, "type": rtype, "missing_required_columns": missing, extra_key: unknown},
                         indent=2, ensure_ascii=False))
        return 1
    for chunk in iter_raw(path, DEFAULT_POLICY.chunk_rows):
        g, bad, m = clean(chunk, contract)
        rows, good, rejected, dups = rows + m["rows_in"], good + len(g), rejected + m["rejected"], dups + m["duplicates_in_batch"]
        for r, n in (bad["reject_reason"].value_counts().items() if len(bad) else []):
            reasons[r] = reasons.get(r, 0) + int(n)
    print(json.dumps({"file": path.name, "type": rtype, "rows": rows, "valid_rows": good, "rejected": rejected,
                      "duplicate_keys_in_file": dups, "reject_reasons": reasons, "missing_required_columns": missing,
                      "columns_that_will_be_ignored" if contract.strict_columns else "extra_columns": unknown},
                     indent=2, ensure_ascii=False))
    return 0 if not missing and rejected == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--simulate", type=int, default=None, help="generate N simulated sessions into landing")
    r.add_argument("--wait", type=float, default=0.0, help="wait up to SEC seconds if another run holds the lock")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--no-isolate", action="store_true", help="run steps in-process (faster start, but timeouts are not enforced)")
    sub.add_parser("status")
    c = sub.add_parser("check", help="validate a file against a data contract WITHOUT ingesting it")
    c.add_argument("--file", required=True)
    c.add_argument("--type", required=True, choices=["session", "price_observation", "review", "fx_rate", "branch_cost"])
    args = ap.parse_args()
    layout = Layout(DATA_DIR)

    if args.cmd == "check":
        return check_file(Path(args.file), args.type)
    if args.cmd == "status":
        p = layout.state / "last_run.json"
        print(p.read_text(encoding="utf-8") if p.exists() else "No runs yet.")
        return 0
    try:
        report = run_pipeline(layout, simulate_n=args.simulate, workers=args.workers, wait_lock=args.wait,
                              isolate=False if args.no_isolate else None)
    except PipelineBusy as exc:
        print(f"BUSY: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
