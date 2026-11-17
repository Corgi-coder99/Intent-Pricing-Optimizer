from typing import Dict
from src.ai_extractor import ReviewSignalExtractor
from src.config import PROCESSED_DATASET_PATH
from src.data_generator import generate_ecommerce_dataset
from src.ml_pricing_model import DynamicPricingModel


def run_full_pipeline(n_samples: int = 50000, use_llm: bool = False) -> Dict[str, object]:
    """Generate data -> Tier 1 AI extraction -> train Tier 2 LightGBM."""
    print("[1/3] Generating synthetic e-commerce session & review data...")
    raw_df = generate_ecommerce_dataset(n_samples=n_samples, save=True)

    print("[2/3] Running Tier 1 AI Review Signal Extractor...")
    processed_df = ReviewSignalExtractor(use_llm=use_llm).enrich_dataframe(raw_df)
    processed_df.to_csv(PROCESSED_DATASET_PATH, index=False)

    print("[3/3] Training Tier 2 LightGBM Dynamic Pricing & Conversion Model...")
    metrics = DynamicPricingModel().train(processed_df)
    print(f"Pipeline complete! Validation ROC-AUC: {metrics['roc_auc']}")

    return {
        "rows": len(processed_df),
        "roc_auc": metrics["roc_auc"],
        "processed_path": str(PROCESSED_DATASET_PATH),
    }


if __name__ == "__main__":
    run_full_pipeline()
