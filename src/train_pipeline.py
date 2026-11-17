import hashlib
from pathlib import Path
from typing import Dict
import pandas as pd
from src.config import DATA_DIR, MODEL_ARTIFACT_PATH, PROCESSED_DATASET_PATH
from src.ml_pricing_model import DynamicPricingModel
from src.pipeline import Layout, run_pipeline
from src.pipeline.infra import atomic_write_json, fingerprint, read_json


def run_full_pipeline(n_samples: int = 50000, use_llm: bool = False, wait_lock: float = 600.0,
                      force: bool = False, isolate: bool = True) -> Dict[str, object]:
    """landing -> bronze -> silver -> gold (src.pipeline), then train Tier 2 LightGBM from the gold table.
    Training is skipped when neither the gold data nor the model code changed (use force=True to retrain).
    isolate=False runs the steps in this process (no per-step timeouts): needed inside Streamlit, whose script cannot be re-imported by child processes."""
    print("[1/2] Running bronze -> silver -> gold pipeline ...")
    layout = Layout(DATA_DIR)
    report = run_pipeline(layout, simulate_n=n_samples, use_llm=use_llm, wait_lock=wait_lock, isolate=isolate)
    if not report["ok"]:
        raise RuntimeError(f"Pipeline failed: {report['status']} {report['results']}")

    gold_path = layout.gold / "sessions_features.parquet"
    gold = pd.read_parquet(gold_path)
    gold.to_csv(PROCESSED_DATASET_PATH, index=False)   # compat for dashboard/API/old tests

    code = Path(__file__).with_name("ml_pricing_model.py").read_bytes()
    fp = hashlib.sha256(fingerprint([gold_path]).encode() + code).hexdigest()
    meta_path = layout.state / "model_meta.json"
    meta = read_json(meta_path, {})
    if not force and meta.get("fingerprint") == fp and MODEL_ARTIFACT_PATH.exists():
        print(f"[2/2] Model up to date (data and code unchanged), skipping training. ROC-AUC: {meta['roc_auc']}")
        return {"rows": len(gold), "roc_auc": meta["roc_auc"], "trained": False, "processed_path": str(PROCESSED_DATASET_PATH)}

    print("[2/2] Training Tier 2 LightGBM from gold table ...")
    metrics = DynamicPricingModel().train(gold)
    atomic_write_json(meta_path, {"fingerprint": fp, **metrics})
    print(f"Pipeline complete! Validation ROC-AUC: {metrics['roc_auc']}")
    return {"rows": len(gold), "roc_auc": metrics["roc_auc"], "trained": True, "processed_path": str(PROCESSED_DATASET_PATH)}


if __name__ == "__main__":
    run_full_pipeline()
