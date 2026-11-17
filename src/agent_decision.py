from typing import Dict, List
import pandas as pd
from src.config import OPENAI_API_KEY
from src.i18n import fr_typography

STRATEGIES = {
    "defensive": "Defensive Conversion Recovery",
    "markup": "Margin Expansion (Dynamic Markup)",
    "hold": "Equilibrium Hold & Targeted Intervention",
}
STRATEGY_LABELS_FR = {
    "defensive": "Redressement défensif de la conversion",
    "markup": "Hausse de marge (majoration dynamique)",
    "hold": "Maintien de l'équilibre et action ciblée",
}
COMPLAINT_LABELS_FR = {
    "price_value": "rapport qualité-prix",
    "shipping_packaging": "livraison et emballage",
    "quality_defect": "défaut de qualité",
}


def money(value: float, lang: str = "en", decimals: int = 2) -> str:
    """EN: €1,234.56   FR: 1 234,56 € (narrow no-break space as thousands separator)."""
    text = f"{value:,.{decimals}f}"
    if lang == "fr":
        return text.replace(",", " ").replace(".", ",") + " €"
    return "€" + text


def number(value: float, lang: str = "en", decimals: int = 1) -> str:
    text = f"{value:.{decimals}f}"
    return text.replace(".", ",") if lang == "fr" else text


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
        lang: str = "en",
    ) -> Dict[str, str]:
        lang = "fr" if lang == "fr" else "en"
        best_row = sim_df.loc[sim_df["expected_revenue_per_100_visits"].idxmax()]
        optimal_price = float(best_row["offered_price"])
        optimal_discount = float(best_row["discount_pct"])
        expected_conv = float(best_row["conversion_prob"]) * 100.0
        expected_rev = float(best_row["expected_revenue_per_100_visits"])

        if lang == "fr":
            complaint_summary = (", ".join(COMPLAINT_LABELS_FR.get(c, c) for c in top_complaints)
                                 if top_complaints else "aucun frein majeur détecté")
        else:
            complaint_summary = ", ".join(top_complaints) if top_complaints else "no major friction detected"

        sent, conv, disc = number(current_sentiment, lang, 2), number(expected_conv, lang), number(abs(optimal_discount), lang)
        p_new, p_old, rev = money(optimal_price, lang), money(base_price, lang), money(expected_rev, lang)

        if current_sentiment < 0.45 and optimal_discount > 2.0:
            key = "defensive"
            rationale = {
                "en": (f"Customer sentiment for {product_name} is pressured ({sent}/1.0) due to [{complaint_summary}]. "
                       f"Recommend a targeted {disc}% discount to {p_new} (from {p_old}), lifting predicted conversion to "
                       f"{conv}% and maximizing expected revenue at {rev} per 100 visits."),
                "fr": (f"Le sentiment client pour {product_name} est dégradé ({sent}/1,0) en raison de : {complaint_summary}. "
                       f"Nous recommandons une remise ciblée de {disc} %, soit {p_new} au lieu de {p_old}. La conversion "
                       f"prévue passe à {conv} %, pour un chiffre d'affaires attendu maximal de {rev} pour 100 visites."),
            }[lang]
        elif optimal_discount < -2.0:
            key = "markup"
            rationale = {
                "en": (f"Healthy sentiment ({sent}/1.0) and low price sensitivity support a {disc}% price increase to "
                       f"{p_new}, expanding gross margin while maintaining strong conversion ({conv}%)."),
                "fr": (f"Un sentiment sain ({sent}/1,0) et une faible sensibilité au prix justifient une hausse de {disc} % (à "
                       f"{p_new}), qui élargit la marge brute tout en maintenant une forte conversion ({conv} %)."),
            }[lang]
        else:
            key = "hold"
            rationale = {
                "en": (f"Optimal revenue point sits near base price ({p_new}). Instead of broad margin cuts, deploy an "
                       f"exit-intent assistant addressing '{complaint_summary}'."),
                "fr": (f"Le chiffre d'affaires est optimal près du prix de base ({p_new}). Plutôt que de larges "
                       f"baisses de marge, nous recommandons un assistant déclenché à l'intention de sortie, qui traite : « {complaint_summary} »."),
            }[lang]

        if lang == "fr":
            rationale = fr_typography(rationale)
        summary_text = rationale
        if self.use_llm:
            summary_text = self._polish_with_llm(
                product_name, STRATEGIES[key], optimal_price, expected_conv, complaint_summary, rationale, lang
            )

        return {
            "strategy_name": STRATEGIES[key],                      # stable English identifier
            "strategy_label": STRATEGY_LABELS_FR[key] if lang == "fr" else STRATEGIES[key],
            "recommended_price": money(optimal_price, lang),
            "optimal_discount": (f"{optimal_discount:+.1f}%" if lang == "en" else f"{optimal_discount:+.1f}".replace(".", ",") + "\u00a0%"),
            "expected_conversion": f"{conv}%" if lang == "en" else f"{conv} %",
            "expected_revenue_100": money(expected_rev, lang),
            "executive_summary": summary_text,
        }

    def _polish_with_llm(self, product_name, strategy, optimal_price, expected_conv, complaint_summary,
                         fallback_text, lang="en") -> str:
        try:
            language = "French" if lang == "fr" else "English"
            prompt = (
                f"You are a Chief Revenue Officer AI. Write a concise 2-sentence executive action brief in {language} "
                f"for '{product_name}'. Strategy: {strategy}. Optimal Price: {optimal_price:.2f} EUR. "
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
