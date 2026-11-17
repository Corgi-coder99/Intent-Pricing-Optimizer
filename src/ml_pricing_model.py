from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import os
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from src.config import FEATURE_COLUMNS, MODEL_ARTIFACT_PATH, RANDOM_SEED


class DynamicPricingModel:
    """Tier 2: predicts conversion probability across price variations."""

    def __init__(self, model_path: Path = MODEL_ARTIFACT_PATH):
        self.model_path = Path(model_path)
        self.model: Optional[lgb.Booster] = None
        self.feature_cols: List[str] = FEATURE_COLUMNS

    def train(self, df: pd.DataFrame) -> Dict[str, float]:
        X = df[self.feature_cols]
        y = df["converted"]
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=RANDOM_SEED, stratify=y
        )
        self.model = lgb.LGBMClassifier(
            n_estimators=250,
            learning_rate=0.05,
            num_leaves=16,
            min_child_samples=300,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.9,
            reg_lambda=5.0,
            random_state=RANDOM_SEED,
            verbose=-1,
            # Economic constraints: higher price/ratio -> conversion does not rise; better sentiment -> does not fall
            # order: base_price, offered_price, discount_pct, comp_ratio, sentiment, pain, views, cart
            monotone_constraints=[0, -1, 1, -1, 1, -1, 1, 1],
        )
        self.model.fit(X_train, y_train)
        preds = self.model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, preds)
        # Saved in LightGBM's native text format (NO pickle): loading this file cannot execute arbitrary code.
        tmp = self.model_path.with_name(self.model_path.name + f".{os.getpid()}.tmp")
        self.model.booster_.save_model(str(tmp))
        os.replace(tmp, self.model_path)   # atomic: the API reading the file never sees a half-written one
        self.model = lgb.Booster(model_file=str(self.model_path))
        return {"roc_auc": round(float(auc), 4)}

    @property
    def version(self) -> str:
        """Model version (SHA-256 of the file): two machines with the same version give the same output for the same input."""
        return hashlib.sha256(self.model_path.read_bytes()).hexdigest()[:12] if self.model_path.exists() else "none"

    def load(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model artifact not found at {self.model_path}. Run src/train_pipeline.py first."
            )
        self.model = lgb.Booster(model_file=str(self.model_path))

    def simulate_price_curve(self, product_context: Dict[str, float]) -> pd.DataFrame:
        """Simulates conversion probability and expected revenue from -20% to +15% of base price."""
        if self.model is None:
            self.load()

        base_price = float(product_context["base_price"])
        comp_price = float(product_context["competitor_price"])
        rows = []
        for mult in np.linspace(0.80, 1.15, 15):
            sim_price = round(base_price * mult, 2)
            rows.append({
                "base_price": base_price,
                "offered_price": sim_price,
                "discount_pct": round((1.0 - mult) * 100.0, 2),
                "competitor_price_ratio": round(sim_price / max(comp_price, 0.01), 3),
                "ai_sentiment_score": float(product_context["ai_sentiment_score"]),
                "pain_point_severity": float(product_context["pain_point_severity"]),
                "session_views": float(product_context["session_views"]),
                "cart_additions_7d": float(product_context["cart_additions_7d"]),
            })

        sim_df = pd.DataFrame(rows)
        probs = self.model.predict(sim_df[self.feature_cols])
        sim_df["conversion_prob"] = np.round(probs, 4)
        sim_df["expected_revenue_per_100_visits"] = np.round(
            sim_df["offered_price"] * sim_df["conversion_prob"] * 100.0, 2
        )
        return sim_df[["offered_price", "discount_pct", "conversion_prob", "expected_revenue_per_100_visits"]]
