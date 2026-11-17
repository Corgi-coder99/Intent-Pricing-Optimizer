"""DAG orchestration: detects cycles before running, runs independent steps in parallel,
each step runs at most once per run, a failed/timed-out step causes its dependents to be skipped (no automatic retry).

Each step runs in a child process (spawn) so a timeout is a real kill and no thread is left running in the background.
"""
import multiprocessing as mp
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.pipeline import nodes
from src.pipeline.infra import Layout, RunLock, atomic_write_json, cleanup_tmp, fingerprint, read_json
from src.pipeline.nodes import Context
from src.pipeline.policy import DEFAULT_POLICY, Policy

HEARTBEAT_SECONDS = 5.0


class CycleError(ValueError):
    pass


class NodeTimeout(RuntimeError):
    pass


class InsufficientResources(RuntimeError):
    pass


@dataclass
class Node:
    name: str
    fn: Callable[[Context], Dict]
    deps: Tuple[str, ...] = ()
    inputs: Optional[Callable[[Layout], List[Path]]] = None    # if set: skipped when the inputs have not changed
    outputs: Optional[Callable[[Layout], List[Path]]] = None
    timeout: float = 300.0                                     # seconds; only enforced when isolate=True


def build_nodes(use_llm: bool = False) -> Dict[str, Node]:
    from src import ai_extractor as _extractor_module
    EXTRACTOR_SRC = Path(_extractor_module.__file__)
    S = lambda L: [L.silver / "session.parquet", EXTRACTOR_SRC]     # changing the extractor code -> gold is recomputed
    G = lambda L: [L.gold / "sessions_features.parquet"]
    # Measured on 50k rows: every step < 2s. Timeouts are set ~50-100x higher to catch hung steps without killing legitimately slow ones.
    ns = [
        Node("generate_simulated", nodes.generate_simulated, timeout=120),
        Node("bronze_ingest", nodes.bronze_ingest, ("generate_simulated",), timeout=120),
        Node("silver_session", partial(nodes.silver_build, rtype="session"), ("bronze_ingest",), timeout=300),
        Node("silver_price_observation", partial(nodes.silver_build, rtype="price_observation"), ("bronze_ingest",), timeout=300),
        Node("silver_review", partial(nodes.silver_build, rtype="review"), ("bronze_ingest",), timeout=300),
        Node("gold_sessions", nodes.gold_sessions, ("silver_session",), S, G, timeout=1800 if use_llm else 300),
        Node("gold_summaries", nodes.gold_summaries, ("gold_sessions",), G,
             lambda L: [L.gold / "region_summary.parquet", L.gold / "category_summary.parquet"], timeout=120),
        Node("silver_fx_rate", partial(nodes.silver_build, rtype="fx_rate"), ("bronze_ingest",), timeout=300),
        Node("silver_branch_cost", partial(nodes.silver_build, rtype="branch_cost"), ("bronze_ingest",), timeout=300),
        Node("housekeeping", nodes.housekeeping,
             ("silver_session", "silver_price_observation", "silver_review", "silver_fx_rate", "silver_branch_cost"),
             timeout=300),
        Node("gold_competitor_prices", nodes.gold_competitor_prices, ("silver_price_observation",), timeout=120),
        Node("gold_market_margin", nodes.gold_market_margin,
             ("gold_competitor_prices", "silver_fx_rate", "silver_branch_cost"), timeout=120),
    ]
    return {n.name: n for n in ns}


def topological_levels(graph: Dict[str, Node]) -> List[List[str]]:
    """Kahn: returns the levels that can run in parallel; raises CycleError on a cycle or a missing dependency."""
    for n in graph.values():
        for d in n.deps:
            if d not in graph:
                raise CycleError(f"{n.name} depends on a step that does not exist: {d}")
    indeg = {k: len(v.deps) for k, v in graph.items()}
    done, levels = set(), []
    while len(done) < len(graph):
        level = sorted(k for k, d in indeg.items() if d == 0 and k not in done)
        if not level:
            raise CycleError("Dependency cycle detected: " + ", ".join(sorted(set(graph) - done)))
        levels.append(level)
        done.update(level)
        for k, v in graph.items():
            if k not in done:
                indeg[k] = sum(1 for d in v.deps if d not in done)
    return levels


def _child(fn, ctx, conn) -> None:
    try:
        conn.send(("ok", fn(ctx)))
    except BaseException as exc:                      # noqa: BLE001 - report the error back to the parent process
        conn.send(("err", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def run_isolated(node: Node, ctx: Context) -> Dict:
    """Run a step in a child process; kill it on timeout. Writes are atomic so a kill cannot corrupt data."""
    spawn = mp.get_context("spawn")
    parent, child = spawn.Pipe(duplex=False)
    proc = spawn.Process(target=_child, args=(node.fn, ctx, child), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(node.timeout):
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join(5)
            raise NodeTimeout(f"{node.name} exceeded {node.timeout:.0f}s and was killed")
        try:
            kind, payload = parent.recv()
        except EOFError:
            proc.join(5)
            raise RuntimeError(f"{node.name} process died (exit code {proc.exitcode})")
        proc.join(10)
        if kind == "err":
            raise RuntimeError(payload)
        return payload
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()


def run_pipeline(layout: Layout, simulate_n: Optional[int] = None, use_llm: bool = False,
                 workers: int = 3, wait_lock: float = 0.0, graph: Optional[Dict[str, Node]] = None,
                 isolate: Optional[bool] = None, policy: Policy = DEFAULT_POLICY) -> Dict:
    """isolate=None: on for the default graph (timeouts enforced), off for custom graphs using lambdas (not picklable)."""
    isolate = (graph is None) if isolate is None else isolate
    graph = graph or build_nodes(use_llm)
    topological_levels(graph)               # fail early on a cycle, before touching any data
    layout.ensure()
    free = shutil.disk_usage(layout.root).free
    if free < policy.min_free_bytes:                # circuit breaker: refuse to run on a nearly full disk to avoid corrupting data
        raise InsufficientResources(f"Only {free // 2**20} MiB free; need {policy.min_free_bytes // 2**20} MiB")
    ctx = Context(layout=layout, simulate_n=simulate_n, use_llm=use_llm, policy=policy)
    fp_path = layout.state / "fingerprints.json"

    with RunLock(layout.state / "pipeline.lock", wait=wait_lock) as lock:
        cleanup_tmp(layout)
        fps: Dict[str, str] = read_json(fp_path, {})
        status: Dict[str, str] = {}
        results: Dict[str, dict] = {}
        durations: Dict[str, float] = {}
        started: set = set()
        t0 = time.time()

        def execute(node: Node) -> Dict:
            t = time.time()
            try:
                if node.inputs and node.outputs:
                    fp = fingerprint(node.inputs(layout))
                    if fps.get(node.name) == fp and all(p.exists() for p in node.outputs(layout)):
                        return {"__skipped__": "inputs unchanged"}
                    out = run_isolated(node, ctx) if isolate else node.fn(ctx)
                    fps[node.name] = fp
                    return out
                return run_isolated(node, ctx) if isolate else node.fn(ctx)
            finally:
                durations[node.name] = round(time.time() - t, 2)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            while len(status) < len(graph):
                for name, node in graph.items():
                    if name in started:
                        continue
                    dep_states = [status.get(d) for d in node.deps]
                    if any(s in ("failed", "skipped_dependency") for s in dep_states):
                        status[name] = "skipped_dependency"
                        started.add(name)
                    elif all(s in ("ok", "unchanged") for s in dep_states):
                        started.add(name)
                        futures[pool.submit(execute, node)] = name
                if not futures:
                    break
                finished, _ = wait(list(futures), timeout=HEARTBEAT_SECONDS, return_when=FIRST_COMPLETED)
                lock.heartbeat()            # still alive -> keep the lock, even while a step runs for a long time
                for fut in finished:
                    name = futures.pop(fut)
                    try:
                        res = fut.result()
                        status[name] = "unchanged" if "__skipped__" in res else "ok"
                        results[name] = res
                    except Exception as exc:        # an error/timeout in one step does not crash the whole run
                        status[name] = "failed"
                        results[name] = {"error": f"{type(exc).__name__}: {exc}"}
                        if isinstance(exc, NodeTimeout):
                            results[name]["timed_out"] = True

        atomic_write_json(fp_path, fps)
        report = {"ok": all(s in ("ok", "unchanged") for s in status.values()), "status": status,
                  "results": results, "durations": durations, "seconds": round(time.time() - t0, 2)}
        atomic_write_json(layout.state / "last_run.json", report)
        return report
