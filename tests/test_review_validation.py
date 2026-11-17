"""Fast synthetic checks for offline review validation."""

import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from src.data_generator import NEGATIVE_REVIEWS_BY_ISSUE, POSITIVE_REVIEWS
from src.review_validation import load_reviews, validate


def write_csv(tmp_path, texts, ratings):
    path = tmp_path / "reviews.csv"
    pd.DataFrame({"body": texts, "stars": ratings}).to_csv(path, index=False, encoding="utf-8")
    return path


def test_synthetic_accuracy_and_privacy(tmp_path):
    negative = [text for group in NEGATIVE_REVIEWS_BY_ISSUE.values() for text in group]
    texts = POSITIVE_REVIEWS + negative
    path = write_csv(tmp_path, texts, [5] * len(POSITIVE_REVIEWS) + [1] * len(negative))
    report = validate(load_reviews(path, "body", "stars"))
    assert report["n"] == len(texts)
    assert report["binary_accuracy"] >= 0.9
    assert report["confusion"]["negative"]["negative"] == len(negative)
    assert report["confusion"]["positive"]["positive"] >= 3
    assert report["majority_baseline_accuracy"] == len(negative) / len(texts)
    assert report["spearman_sentiment_vs_rating"] > 0.7
    assert report["verdict"] == "usable"
    encoded = json.dumps(report, allow_nan=False)
    assert all(text not in encoded for text in texts)


def test_no_keywords():
    report = validate(pd.DataFrame({"text": ["An ordinary item.", "It is blue."], "rating": [1, 5]}))
    assert report["coverage"] == 0.0
    assert report["verdict"] == "illustrative_only"
    assert report["binary_accuracy"] == 0.0
    assert report["spearman_sentiment_vs_rating"] is None


def test_filtering_and_limit(tmp_path):
    path = write_csv(tmp_path, ["bad"] * 3 + ["", "   ", " great ", "broken"], [0, 6, "abc", 5, 1, 4.6, 1])
    df = load_reviews(path, "body", "stars")
    assert df.to_dict("list") == {"text": ["great", "broken"], "rating": [5, 1]}
    assert validate(df)["n"] == 2
    assert len(load_reviews(path, "body", "stars", max_rows=1)) == 1
    with pytest.raises(ValueError, match="No valid reviews"):
        load_reviews(path, "body", "stars", max_rows=0)


def test_neutral_and_confusion():
    report = validate(pd.DataFrame({"text": ["An ordinary item."], "rating": [3]}))
    labels = {"negative", "neutral", "positive"}
    assert set(report["confusion"]) == labels
    assert all(set(row) == labels for row in report["confusion"].values())
    assert report["confusion"]["neutral"]["neutral"] == 1
    assert report["polarity_accuracy"] == 1.0
    assert report["binary_accuracy"] is None
    assert report["mean_sentiment_by_rating"] == {"1": None, "2": None, "3": 0.5, "4": None, "5": None}


def test_missing_column_and_empty_data(tmp_path):
    path = write_csv(tmp_path, ["great"], [0])
    with pytest.raises(ValueError, match="Missing required column"):
        load_reviews(path, "absent", "stars")
    with pytest.raises(ValueError, match="No valid reviews"):
        load_reviews(path, "body", "stars")


def test_cache_and_threshold_boundaries():
    class Extractor:
        def __init__(self):
            self.calls = []

        def analyze_single_review(self, text):
            self.calls.append(text)
            return {"ai_sentiment_score": {"low": 0.45, "high": 0.55}[text], "complaint_category": "none"}

    extractor = Extractor()
    report = validate(pd.DataFrame({"text": ["low", "high", "low"], "rating": [1, 5, 3]}), extractor=extractor)
    assert extractor.calls == ["low", "high"]
    assert sum(row["neutral"] for row in report["confusion"].values()) == 3
    assert report["complaint_category_counts"] == {"none": 3}


def test_cli(tmp_path):
    path = write_csv(tmp_path, ["great", "broken"], [5, 1])
    command = [sys.executable, "-m", "src.review_validation", "--file", str(path), "--text-col", "body", "--rating-col", "stars"]
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["n"] == 2
    command[4] = str(tmp_path / "missing.csv")
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "Unable to load reviews" in result.stderr
    assert result.stderr.isascii()
