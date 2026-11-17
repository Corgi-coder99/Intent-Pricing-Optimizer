"""Offline review validation against customer star ratings."""

import argparse
from collections import Counter
import json
import sys

import pandas as pd

from src.ai_extractor import ReviewSignalExtractor


def load_reviews(path, text_col, rating_col, max_rows=None):
    """Load UTF-8 CSV reviews, retaining valid text and ratings in 1..5."""
    raw = pd.read_csv(path, encoding="utf-8")
    for column in (text_col, rating_col):
        if column not in raw.columns:
            raise ValueError(f"Missing required column: {column}")
    text = raw[text_col].astype("string").str.strip()
    rating = pd.to_numeric(raw[rating_col], errors="coerce")
    valid = text.notna() & text.ne("") & rating.between(1, 5)
    result = pd.DataFrame({"text": text[valid], "rating": rating[valid].round().astype(int)})
    if max_rows is not None:
        if max_rows < 0:
            raise ValueError("max_rows must be non-negative")
        result = result.head(max_rows)
    if result.empty:
        raise ValueError("No valid reviews remain after filtering")
    return result.reset_index(drop=True)


def validate(df, neg_threshold=0.45, pos_threshold=0.55, extractor=None):
    """Return text-free metrics, analyzing each unique text once.

    Ratings 1-2 are negative, 3 neutral, and 4-5 positive. Scores below
    neg_threshold (default 0.45) are negative, above pos_threshold (0.55)
    positive, and otherwise neutral, including both boundaries. Binary
    accuracy excludes true neutral ratings; neutral predictions count as errors.
    A usable verdict requires binary accuracy >= 0.75 and coverage >= 0.5.
    """
    if df.empty:
        raise ValueError("No reviews to validate")
    if not 0 <= neg_threshold <= pos_threshold <= 1:
        raise ValueError("Thresholds must satisfy 0 <= negative <= positive <= 1")
    if extractor is None:
        extractor = ReviewSignalExtractor(use_llm=False)
    cache = {text: extractor.analyze_single_review(text) for text in df["text"].unique()}
    signals = [cache[text] for text in df["text"]]
    scores = pd.Series([float(signal["ai_sentiment_score"]) for signal in signals])
    ratings = df["rating"].reset_index(drop=True)
    labels = ("negative", "neutral", "positive")
    true = ["negative" if r <= 2 else "positive" if r >= 4 else "neutral" for r in ratings]
    predicted = ["negative" if s < neg_threshold else "positive" if s > pos_threshold else "neutral" for s in scores]
    confusion = {actual: {pred: 0 for pred in labels} for actual in labels}
    for actual, pred in zip(true, predicted):
        confusion[actual][pred] += 1
    n = len(true)
    binary_pairs = [(a, p) for a, p in zip(true, predicted) if a != "neutral"]
    binary_accuracy = sum(a == p for a, p in binary_pairs) / len(binary_pairs) if binary_pairs else None
    coverage = sum(s["complaint_category"] != "none" or float(s["ai_sentiment_score"]) != 0.5 for s in signals) / n
    correlation = scores.corr(ratings, method="spearman") if scores.nunique() > 1 and ratings.nunique() > 1 else None
    return {
        "n": n,
        "polarity_accuracy": sum(a == p for a, p in zip(true, predicted)) / n,
        "binary_accuracy": binary_accuracy,
        "majority_baseline_accuracy": max(Counter(true).values()) / n,
        "confusion": confusion,
        "coverage": coverage,
        "mean_sentiment_by_rating": {str(r): float(scores[ratings == r].mean()) if (ratings == r).any() else None for r in range(1, 6)},
        "spearman_sentiment_vs_rating": None if correlation is None or pd.isna(correlation) else float(correlation),
        "complaint_category_counts": dict(Counter(s["complaint_category"] for s in signals)),
        "verdict": "usable" if binary_accuracy is not None and binary_accuracy >= 0.75 and coverage >= 0.5 else "illustrative_only",
    }


def main():
    """Print validation JSON, or an ASCII loading error with exit code 2."""
    parser = argparse.ArgumentParser(description="Validate offline review signals against star ratings.")
    parser.add_argument("--file", required=True)
    parser.add_argument("--text-col", required=True)
    parser.add_argument("--rating-col", required=True)
    parser.add_argument("--max-rows", type=int)
    args = parser.parse_args()
    try:
        df = load_reviews(args.file, args.text_col, args.rating_col, args.max_rows)
    except (OSError, ValueError) as exc:
        message = f"Unable to load reviews: {exc}".encode("ascii", errors="backslashreplace").decode("ascii")
        print(message, file=sys.stderr)
        return 2
    print(json.dumps(validate(df), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
