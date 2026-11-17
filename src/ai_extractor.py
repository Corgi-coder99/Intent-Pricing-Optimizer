import json
from typing import Dict, List
import pandas as pd
from src.config import OPENAI_API_KEY, PROCESSED_DATASET_PATH, RAW_DATASET_PATH


class ReviewSignalExtractor:
    """Tier 1 AI Pipeline: unstructured review -> `ai_sentiment_score`,
    `pain_point_severity`, `complaint_category` (LLM if available, else offline lexicon)."""

    ISSUE_KEYWORDS: Dict[str, List[str]] = {
        "price_value": ["overpriced", "expensive", "price", "pricier", "justify"],
        "shipping_packaging": ["late", "crushed", "shipping", "tracking", "packaging", "seal"],
        "quality_defect": ["drains", "stopped", "defect", "loose", "flimsier", "broken"],
    }

    POSITIVE_KEYWORDS: List[str] = [
        "exceeds", "seamlessly", "worth", "fast", "great", "reliable", "sleek", "essential"
    ]

    def __init__(self, use_llm: bool = False):
        self.use_llm = bool(use_llm and OPENAI_API_KEY)
        if self.use_llm:
            from openai import OpenAI
            self.client = OpenAI(api_key=OPENAI_API_KEY)

    def analyze_single_review(self, text: str) -> Dict[str, object]:
        if self.use_llm:
            return self._analyze_with_llm(text)
        return self._analyze_offline(text)

    def _analyze_offline(self, text: str) -> Dict[str, object]:
        text_lower = text.lower()
        detected_category = "none"
        neg_hits = 0

        for category, keywords in self.ISSUE_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in text_lower)
            if hits > 0:
                detected_category = category
                neg_hits += hits

        pos_hits = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in text_lower)

        raw_score = 0.50 + (0.18 * pos_hits) - (0.22 * neg_hits)
        sentiment_score = round(max(0.05, min(0.95, raw_score)), 3)
        severity = round(min(1.0, neg_hits * 0.35), 3) if detected_category != "none" else 0.05

        return {
            "ai_sentiment_score": sentiment_score,
            "pain_point_severity": severity,
            "complaint_category": detected_category,
        }

    def _analyze_with_llm(self, text: str) -> Dict[str, object]:
        prompt = (
            "Analyze this e-commerce product review and return valid JSON with keys: "
            "'ai_sentiment_score' (float 0 to 1), 'pain_point_severity' (float 0 to 1), "
            "and 'complaint_category' (one of: 'none', 'price_value', 'shipping_packaging', 'quality_defect').\n"
            f"Review: {text}"
        )
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        return json.loads(response.choices[0].message.content)

    def enrich_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        unique_reviews = df["recent_review_text"].unique()
        cache = {rev: self.analyze_single_review(rev) for rev in unique_reviews}
        extracted_df = pd.DataFrame([cache[rev] for rev in df["recent_review_text"]], index=df.index)
        return pd.concat([df, extracted_df], axis=1)


if __name__ == "__main__":
    raw_df = pd.read_csv(RAW_DATASET_PATH)
    processed_df = ReviewSignalExtractor(use_llm=False).enrich_dataframe(raw_df)
    processed_df.to_csv(PROCESSED_DATASET_PATH, index=False)
    print(f"Enriched dataset saved to {PROCESSED_DATASET_PATH}")
