from typing import Dict, List
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class ReviewRAGEngine:
    """Tier 1.5 Retrieval Engine: TF-IDF index over reviews to retrieve customer quotes
    explaining conversion friction for a product (optionally filtered by region)."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy().reset_index(drop=True)
        self.vectorizer = TfidfVectorizer(stop_words="english", max_features=1000)
        self.tfidf_matrix = self.vectorizer.fit_transform(self.df["recent_review_text"].fillna(""))

    def retrieve_product_evidence(
        self,
        product_id: str,
        focus_query: str = "expensive late quality broken",
        top_k: int = 3,
        region: str = None,
    ) -> Dict[str, object]:
        prod_mask = (self.df["product_id"] == product_id).values
        if region and "region" in self.df.columns:
            prod_mask &= (self.df["region"] == region).values
        if not np.any(prod_mask):
            return {"top_complaints": [], "evidence_quotes": []}

        prod_df = self.df[prod_mask]
        complaints = prod_df[prod_df["complaint_category"] != "none"]["complaint_category"]
        top_complaints: List[str] = complaints.value_counts().head(2).index.tolist()

        query_vec = self.vectorizer.transform([focus_query])
        prod_indices = np.where(prod_mask)[0]
        sims = cosine_similarity(query_vec, self.tfidf_matrix[prod_indices]).flatten()

        unique_quotes: List[Dict[str, object]] = []
        seen_texts = set()
        for local_idx in np.argsort(sims)[::-1]:
            row = prod_df.iloc[local_idx]
            text = str(row["recent_review_text"])
            if text not in seen_texts:
                seen_texts.add(text)
                unique_quotes.append({
                    "review_text": text,
                    "complaint_category": str(row["complaint_category"]),
                    "sentiment_score": float(row["ai_sentiment_score"]),
                })
            if len(unique_quotes) >= top_k:
                break

        return {"top_complaints": top_complaints, "evidence_quotes": unique_quotes}
