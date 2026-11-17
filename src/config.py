import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

# File Paths
RAW_DATASET_PATH = RAW_DATA_DIR / "ecommerce_sessions_raw.csv"
PROCESSED_DATASET_PATH = PROCESSED_DATA_DIR / "ecommerce_processed.csv"
MODEL_ARTIFACT_PATH = PROCESSED_DATA_DIR / "lgbm_pricing.txt"

# Model & Simulation Settings
RANDOM_SEED = 42
FEATURE_COLUMNS = [
    "base_price",
    "offered_price",
    "discount_pct",
    "competitor_price_ratio",
    "ai_sentiment_score",
    "pain_point_severity",
    "session_views",
    "cart_additions_7d",
]

# Optional API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
