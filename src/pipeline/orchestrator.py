"""Điều phối DAG: kiểm tra vòng lặp trước khi chạy, chạy song song các bước độc lập,
mỗi bước chạy tối đa một lần mỗi lượt, bước lỗi làm bỏ qua các bước phụ thuộc (không tự thử lại vô hạn)."""
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.pipeline import nodes
from src.pipeline.infra import Layout, RunLock, atomic_write_json, fingerprint, read_json
from src.pipeline.nodes import Context


class CycleError(ValueError):
    pass


@dataclass
class Node:
    name: str
    fn: Callable[[Context], Dict]
    deps: Tuple[str, ...] = ()
    inputs: Optional[Callable[[Layout], List[Path]]] = None    # nếu có: bỏ qua khi đầu vào không đổi
    outputs: Optional[Callable[[Layout], List[Path]]] = None


def build_nodes() -> Dict[str, Node]:
    S = lambda L: [L.silver / "session.parquet"]
    G = lambda L: [L.gold / "sessions_features.parquet"]
    ns = [
        Node("generate_simulated", nodes.generate_simulated),
        Node("bronze_ingest", lambda c: nodes.bronze_ingest(c), ("generate_simulated",)),
        Node("silver_session", lambda c: nodes.silver_build(c, "session"), ("bronze_ingest",)),
        Node("silver_price_observation", lambda c: nodes.silver_build(c, "price_observation"), ("bronze_ingest",)),
        Node("silver_review", lambda c: nodes.silver_build(c, "review"), ("bronze_ingest",)),
        Node("gold_sessions", nodes.gold_sessions, ("silver_session",), S, G),
        Node("gold_summaries", nodes.gold_summaries, ("gold_sessions",), G,
             lambda L: [L.gold / "region_summary.parquet", L.gold / "category_summary.parquet"]),
        Node("gold_competitor_prices", nodes.gold_competitor_prices, ("silver_price_observation",)),
    ]
    return {n.name: n for n in ns}


def topological_levels(graph: Dict[str, Node]) -> List[List[str]]:
    """Kahn: trả các tầng chạy song song được; ném CycleError nếu có vòng hoặc phụ thuộc không tồn tại."""
    for n in graph.values():
        for d in n.deps:
            if d not in graph:
                raise CycleError(f"{n.name} phụ thuộc bước không tồn tại: {d}")
    indeg = {k: len(v.deps) for k, v in graph.items()}
    done, levels = set(), []
    while len(done) < len(graph):
        level = sorted(k for k, d in indeg.items() if d == 0 and k not in done)
        if not level:
            raise CycleError("Phát hiện vòng phụ thuộc: " + ", ".join(sorted(set(graph) - done)))
        levels.append(level)
        done.update(level)
        for k, v in graph.items():
            if k not in done:
                indeg[k] = sum(1 for d in v.deps if d not in done)
    return levels


def run_pipeline(layout: Layout, simulate_n: Optional[int] = None, use_llm: bool = False,
                 workers: int = 3, wait_lock: float = 0.0, graph: Optional[Dict[str, Node]] = None) -> Dict:
    graph = graph or build_nodes()
    topological_levels(graph)               # fail sớm nếu có vòng lặp, trước khi đụng tới dữ liệu
    layout.ensure()
    ctx = Context(layout=layout, simulate_n=simulate_n, use_llm=use_llm)
    fp_path = layout.state / "fingerprints.json"

    with RunLock(layout.state / "pipeline.lock", wait=wait_lock):
        fps: Dict[str, str] = read_json(fp_path, {})
        status: Dict[str, str] = {}
        results: Dict[str, dict] = {}
        started: set = set()
        t0 = time.time()

        def execute(node: Node) -> Dict:
            if node.inputs and node.outputs:
                fp = fingerprint(node.inputs(layout))
                if fps.get(node.name) == fp and all(p.exists() for p in node.outputs(layout)):
                    return {"__skipped__": "inputs unchanged"}
                out = node.fn(ctx)
                fps[node.name] = fp
                return out
            return node.fn(ctx)

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
                finished, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for fut in finished:
                    name = futures.pop(fut)
                    try:
                        res = fut.result()
                        unchanged = "__skipped__" in res
                        status[name] = "unchanged" if unchanged else "ok"
                        results[name] = res
                    except Exception as exc:        # lỗi một bước không làm sập cả lượt chạy
                        status[name] = "failed"
                        results[name] = {"error": f"{type(exc).__name__}: {exc}"}

        atomic_write_json(fp_path, fps)
        report = {"ok": all(s in ("ok", "unchanged") for s in status.values()), "status": status,
                  "results": results, "seconds": round(time.time() - t0, 2)}
        atomic_write_json(layout.state / "last_run.json", report)
        return report
