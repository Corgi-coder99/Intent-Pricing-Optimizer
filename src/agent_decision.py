from typing import Dict, List
import pandas as pd
from src.config import OPENAI_API_KEY


class PricingDecisionAgent:
    """Tier 3: synthesizes the ML price curve with customer complaints into a strategy."""

    def __init__(self, use_llm: bool = False):
        self.use_llm = bool(use_llm and OPENAI_API_KEY)
        if self.use_llm:
            from openai import OpenAI
            self.client = OpenAI(api_key=OPENAI_API_KEY)

    def recommend_action(
        self,
        product_name: str,
        base_price: float,
        sim_df: pd.DataFrame,
        top_complaints: List[str],
        current_sentiment: float,
    ) -> Dict[str, str]:
        best_row = sim_df.loc[sim_df["expected_revenue_per_100_visits"].idxmax()]
        optimal_price = float(best_row["offered_price"])
        optimal_discount = float(best_row["discount_pct"])
        expected_conv = float(best_row["conversion_prob"]) * 100.0
        expected_rev = float(best_row["expected_revenue_per_100_visits"])

        complaint_summary = ", ".join(top_complaints) if top_complaints else "no major friction detected"

        if current_sentiment < 0.45 and optimal_discount > 2.0:
            strategy = "Defensive Conversion Recovery"
            fallback_rationale = (
                f"Customer sentiment for {product_name} is pressured ({current_sentiment:.2f}/1.0) "
                f"due to [{complaint_summary}]. Recommend a targeted {optimal_discount:.1f}% discount "
                f"to ${optimal_price:.2f} (from ${base_price:.2f}), lifting predicted conversion to "
                f"{expected_conv:.1f}% and maximizing expected revenue at ${expected_rev:,.2f} per 100 visits."
            )
        elif optimal_discount < -2.0:
            strategy = "Margin Expansion (Dynamic Markup)"
            fallback_rationale = (
                f"Healthy sentiment ({current_sentiment:.2f}/1.0) and low price sensitivity support a "
                f"{abs(optimal_discount):.1f}% price increase to ${optimal_price:.2f}, expanding gross "
                f"margin while maintaining strong conversion ({expected_conv:.1f}%)."
            )
        else:
            strategy = "Equilibrium Hold & Targeted Intervention"
            fallback_rationale = (
                f"Optimal revenue point sits near base price (${optimal_price:.2f}). Instead of broad "
                f"margin cuts, deploy an exit-intent assistant addressing '{complaint_summary}'."
            )

        summary_text = fallback_rationale
        if self.use_llm:
            summary_text = self._polish_with_llm(
                product_name, strategy, optimal_price, expected_conv, complaint_summary, fallback_rationale
            )

        return {
            "strategy_name": strategy,
            "recommended_price": f"${optimal_price:.2f}",
            "optimal_discount": f"{optimal_discount:+.1f}%",
            "expected_conversion": f"{expected_conv:.1f}%",
            "expected_revenue_100": f"${expected_rev:,.2f}",
            "executive_summary": summary_text,
        }

    def _polish_with_llm(self, product_name, strategy, optimal_price, expected_conv, complaint_summary, fallback_text) -> str:
        try:
            prompt = (
                f"You are a Chief Revenue Officer AI. Write a concise 2-sentence executive action brief "
                f"for '{product_name}'. Strategy: {strategy}. Optimal Price: ${optimal_price:.2f}. "
                f"Predicted Conversion: {expected_conv:.1f}%. Key Customer Friction: {complaint_summary}."
            )
            resp = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=120,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return fallback_text
