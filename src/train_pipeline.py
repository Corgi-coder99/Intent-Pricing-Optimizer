from typing import Dict
import pandas as pd
from src.config import DATA_DIR, PROCESSED_DATASET_PATH
from src.ml_pricing_model import DynamicPricingModel
from src.pipeline import Layout, run_pipeline


def run_full_pipeline(n_samples: int = 50000, use_llm: bool = False, wait_lock: float = 600.0) -> Dict[str, object]:
    """landing -> bronze -> silver -> gold (src.pipeline), sau đó huấn luyện Tier 2 LightGBM từ bảng gold."""
    print("[1/2] Running bronze -> silver -> gold pipeline ...")
    layout = Layout(DATA_DIR)
    report = run_pipeline(layout, simulate_n=n_samples, use_llm=use_llm, wait_lock=wait_lock)
    if not report["ok"]:
        raise RuntimeError(f"Pipeline failed: {report['status']} {report['results']}")

    gold = pd.read_parquet(layout.gold / "sessions_features.parquet")
    gold.to_csv(PROCESSED_DATASET_PATH, index=False)   # tương thích dashboard/API/test cũ

    print("[2/2] Training Tier 2 LightGBM from gold table ...")
    metrics = DynamicPricingModel().train(gold)
    print(f"Pipeline complete! Validation ROC-AUC: {metrics['roc_auc']}")
    return {"rows": len(gold), "roc_auc": metrics["roc_auc"], "processed_path": str(PROCESSED_DATASET_PATH)}


if __name__ == "__main__":
    run_full_pipeline()
