"""CLI:  python -m src.pipeline run [--simulate N] [--wait SEC]   |   python -m src.pipeline status"""
import argparse
import json
import sys

from src.config import DATA_DIR
from src.pipeline import Layout, PipelineBusy, run_pipeline


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--simulate", type=int, default=None, help="generate N simulated sessions into landing")
    r.add_argument("--wait", type=float, default=0.0, help="wait up to SEC seconds if another run holds the lock")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--no-isolate", action="store_true", help="run steps in-process (faster start, but timeouts are not enforced)")
    sub.add_parser("status")
    args = ap.parse_args()
    layout = Layout(DATA_DIR)

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
