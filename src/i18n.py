"""English / French UI strings for the dashboard. `t(key, lang)` falls back to English, then to the key itself."""
from typing import Dict

LANGS = {"en": "English", "fr": "Français"}

STRINGS: Dict[str, Dict[str, str]] = {
    # ---- shared
    "language": {"en": "Language", "fr": "Langue"},
    "all": {"en": "All", "fr": "Tous"},
    "price_eur": {"en": "Price (€)", "fr": "Prix (€)"},
    # ---- main page
    "title": {"en": "Intent-Driven Pricing & Conversion — BI Console", "fr": "Prix et conversion pilotés par l'intention — Console BI"},
    "subtitle": {"en": "AI (NLP review mining) → Data Science (LightGBM price elasticity) → E-commerce (pricing agent). Simulated data.",
                 "fr": "IA (analyse NLP des avis) → Data Science (élasticité-prix LightGBM) → E-commerce (agent de prix). Données simulées."},
    "category": {"en": "Category", "fr": "Catégorie"},
    "product": {"en": "Product", "fr": "Produit"},
    "region": {"en": "Region", "fr": "Région"},
    "competitor": {"en": "Competitor", "fr": "Concurrent"},
    "market_sim": {"en": "Market simulation (drag to try)", "fr": "Simulation de marché (à ajuster)"},
    "list_price": {"en": "List price (€)", "fr": "Prix catalogue (€)"},
    "competitor_price": {"en": "Competitor {name} price (€)", "fr": "Prix du concurrent {name} (€)"},
    "sentiment_override": {"en": "AI sentiment (override)", "fr": "Sentiment IA (manuel)"},
    "kpi_sessions": {"en": "Sessions", "fr": "Sessions"},
    "kpi_conversion": {"en": "Conversion rate", "fr": "Taux de conversion"},
    "kpi_revenue": {"en": "Simulated revenue", "fr": "Chiffre d'affaires simulé"},
    "kpi_price": {"en": "Recommended price", "fr": "Prix recommandé"},
    "kpi_discount": {"en": "discount {discount}", "fr": "remise {discount}"},
    "kpi_markup": {"en": "markup {discount}", "fr": "hausse {discount}"},
    "cc_conf_ok": {"en": "reliable", "fr": "fiable"},
    "cc_conf_low": {"en": "low", "fr": "faible"},
    "kpi_gain": {"en": "Gain / 100 visits", "fr": "Gain / 100 visites"},
    "g_conversion": {"en": "Predicted conversion (%)", "fr": "Conversion prévue (%)"},
    "g_sentiment": {"en": "AI sentiment", "fr": "Sentiment IA"},
    "g_price_ratio": {"en": "Price vs avg competitor", "fr": "Prix vs concurrents (moy.)"},
    "g_pain": {"en": "Pain-point severity", "fr": "Gravité des frictions"},
    "agent": {"en": "Pricing Agent", "fr": "Agent de prix"},
    "curve_title": {"en": "Price elasticity & expected revenue curve", "fr": "Courbe d'élasticité-prix et de chiffre d'affaires attendu"},
    "rev_100": {"en": "Revenue / 100 visits (€)", "fr": "CA / 100 visites (€)"},
    "conv_pct": {"en": "Conversion (%)", "fr": "Conversion (%)"},
    "revenue_eur": {"en": "Revenue (€)", "fr": "Chiffre d'affaires (€)"},
    "vs_competitors": {"en": "Price vs competitors", "fr": "Prix face aux concurrents"},
    "our_price": {"en": "Recommended (ours)", "fr": "Notre prix recommandé"},
    "price_axis": {"en": "Price (€)", "fr": "Prix (€)"},
    "map_title": {"en": "Revenue & conversion by region (map)", "fr": "Chiffre d'affaires et conversion par région (carte)"},
    "region_chart": {"en": "Conversion & sentiment by region", "fr": "Conversion et sentiment par région"},
    "sentiment_x100": {"en": "Sentiment ×100", "fr": "Sentiment ×100"},
    "category_gap": {"en": "Category comparison: our price vs main competitor (%)", "fr": "Comparaison par catégorie : notre prix vs concurrent principal (%)"},
    "gap_axis": {"en": "Price gap (%)", "fr": "Écart de prix (%)"},
    "complaints": {"en": "Customer complaint mix", "fr": "Répartition des réclamations clients"},
    "voc": {"en": "Voice of Customer (RAG)", "fr": "Voix du client (RAG)"},
    "friction": {"en": "Friction", "fr": "Frein"},
    "none_word": {"en": "None", "fr": "Aucun"},
    "tag": {"en": "Tag", "fr": "Étiquette"},
    "sentiment": {"en": "Sentiment", "fr": "Sentiment"},
    "summary_table": {"en": "Summary by category", "fr": "Synthèse par catégorie"},
    "col_category": {"en": "Category", "fr": "Catégorie"},
    "col_sessions": {"en": "Sessions", "fr": "Sessions"},
    "col_conversion": {"en": "Conversion", "fr": "Conversion"},
    "col_sentiment": {"en": "Avg sentiment", "fr": "Sentiment moyen"},
    "col_pain": {"en": "Avg pain", "fr": "Friction moyenne"},
    "col_revenue": {"en": "Revenue (€)", "fr": "Chiffre d'affaires (€)"},
    # ---- country comparison page
    "cc_title": {"en": "Price and margin comparison across EU/UK countries", "fr": "Comparaison des prix et des marges entre pays UE/Royaume-Uni"},
    "cc_caption": {"en": "Real market prices (Open Prices), net of VAT and converted to EUR. Margins are a HYPOTHETICAL SCENARIO, not any company's real profit.",
                   "fr": "Prix de marché réels (Open Prices), hors TVA et convertis en EUR. Les marges sont un SCÉNARIO HYPOTHÉTIQUE, pas le bénéfice réel d'une entreprise."},
    "cc_no_data": {"en": "The comparison table has not been built yet. Run the pipeline from the project folder:",
                   "fr": "Le tableau de comparaison n'a pas encore été généré. Lancez le pipeline depuis le dossier du projet :"},
    "cc_reliable": {"en": "Reliable data only", "fr": "Données fiables uniquement"},
    "cc_reliable_help": {"en": "Keep rows with ≥2 stores and ≥3 observations", "fr": "Garder les lignes avec au moins 2 magasins et 3 observations"},
    "cc_countries": {"en": "Countries compared with {home}", "fr": "Pays comparés à {home}"},
    "cc_kpi_products": {"en": "Comparable products (≥2 countries)", "fr": "Produits comparables (≥2 pays)"},
    "cc_kpi_countries": {"en": "Countries with data", "fr": "Pays avec données"},
    "cc_kpi_reliable": {"en": "Reliable rows", "fr": "Lignes fiables"},
    "cc_kpi_fx": {"en": "1 GBP = EUR (ECB {date})", "fr": "1 GBP = EUR (BCE {date})"},
    "cc_fx_stale": {"en": "The exchange rate is {days} days old; GBP prices may be converted with an outdated rate. Run `python -m src.connectors.ecb_fx`.",
                    "fr": "Le taux de change date de {days} jours ; les prix en GBP peuvent être convertis avec un taux périmé. Lancez `python -m src.connectors.ecb_fx`."},
    "cc_kpi_fx_none": {"en": "GBP rate", "fr": "Taux GBP"},
    "cc_none": {"en": "none yet", "fr": "pas encore disponible"},
    "cc_empty": {"en": "No product qualifies under the current filters. Untick 'Reliable data only' or add countries.",
                 "fr": "Aucun produit ne correspond aux filtres actuels. Décochez « Données fiables uniquement » ou ajoutez des pays."},
    "cc_gap_title": {"en": "Median price gap vs {home} (%) — n = comparable products", "fr": "Écart de prix médian vs {home} (%) — n = produits comparables"},
    "cc_gap_axis": {"en": "% vs {home}", "fr": "% vs {home}"},
    "cc_margin_title": {"en": "Median scenario margin vs real sector margin by country (Eurostat)",
                        "fr": "Marge de scénario médiane vs marge sectorielle réelle par pays (Eurostat)"},
    "cc_scenario": {"en": "Scenario margin (anchored at home country)", "fr": "Marge de scénario (ancrée sur le pays d'origine)"},
    "cc_sector": {"en": "Food retail sector margin (Eurostat)", "fr": "Marge du commerce de détail alimentaire (Eurostat)"},
    "cc_pct_revenue": {"en": "% of revenue", "fr": "% du chiffre d'affaires"},
    "cc_map_title": {"en": "Map: price gap vs {home}", "fr": "Carte : écart de prix vs {home}"},
    "cc_detail": {"en": "Product detail", "fr": "Détail par produit"},
    "cc_no_products": {"en": "No product has prices in two or more countries yet.", "fr": "Aucun produit n'a encore de prix dans au moins deux pays."},
    "cc_product": {"en": "Product", "fr": "Produit"},
    "cc_generic_product": {"en": "Product", "fr": "Produit"},
    "cc_net_price": {"en": "Net price (EUR)", "fr": "Prix hors TVA (EUR)"},
    "cc_col_country": {"en": "Country", "fr": "Pays"},
    "cc_col_scenario": {"en": "Scenario margin %", "fr": "Marge de scénario (%)"},
    "cc_col_sector": {"en": "Sector margin %", "fr": "Marge sectorielle (%)"},
    "cc_col_obs": {"en": "Observations", "fr": "Observations"},
    "cc_col_stores": {"en": "Stores", "fr": "Magasins"},
    "cc_col_conf": {"en": "Reliability", "fr": "Fiabilité"},
    "cc_howto": {"en": "How to read this page and its limits (read before use)", "fr": "Comment lire cette page et ses limites (à lire avant usage)"},
    "cc_note_1": {"en": "**Scenario margin** assumes every branch buys at the same unit cost, and that this cost gives the home country's sector margin when selling at the home market price. It shows how the margin *would change* when selling at another country's price, not real profit.",
                  "fr": "La **marge de scénario** suppose que chaque filiale achète au même coût unitaire, et que ce coût donne la marge sectorielle du pays d'origine au prix du marché d'origine. Elle montre comment la marge *évoluerait* au prix d'un autre pays ; ce n'est pas un bénéfice réel."},
    "cc_note_2": {"en": "**Sector margin (Eurostat)** is the gross margin of the whole supermarket/food retail sector per country, 2019 (UK: 2018), not any company's cost.",
                  "fr": "La **marge sectorielle (Eurostat)** est la marge brute de l'ensemble du commerce de détail alimentaire par pays, 2019 (Royaume-Uni : 2018), pas le coût d'une entreprise."},
    "cc_note_3": {"en": "**Price data** is crowd-sourced (Open Prices), mostly food, with no accuracy guarantee; few products appear in several countries.",
                  "fr": "Les **données de prix** sont participatives (Open Prices), surtout alimentaires, sans garantie d'exactitude ; peu de produits apparaissent dans plusieurs pays."},
    "cc_note_4": {"en": "**The VAT table is unverified**; exchange rates come from the ECB. With real branch costs, a real-margin column replaces the scenario.",
                  "fr": "**Le tableau des taux de TVA n'est pas vérifié** ; les taux de change proviennent de la BCE. Avec les coûts réels des filiales, une colonne de marge réelle remplacera le scénario."},
    "cc_note_5": {"en": "Sources: Open Food Facts contributors (ODbL), ECB, Eurostat. Details in `docs/DATA_SOURCES.md`.",
                  "fr": "Sources : contributeurs Open Food Facts (ODbL), BCE, Eurostat. Détails dans `docs/DATA_SOURCES.md`."},
}

COMPLAINT_FR = {"none": "aucune", "price_value": "rapport qualité-prix", "shipping_packaging": "livraison et emballage",
                "quality_defect": "défaut de qualité"}


_FR_SPACING = [(" :", "\u00a0:"), (" ;", "\u202f;"), (" ?", "\u202f?"), (" !", "\u202f!"), (" %", "\u00a0%"),
               ("« ", "«\u00a0"), (" »", "\u00a0»")]


def fr_typography(text: str) -> str:
    """French typography: non-breaking space before : ; ? ! % and inside « »."""
    for old, new in _FR_SPACING:
        text = text.replace(old, new)
    return text


def t(key: str, lang: str = "en", **fmt) -> str:
    entry = STRINGS.get(key)
    text = (entry.get(lang) or entry.get("en")) if entry else key
    text = text.format(**fmt) if fmt else text
    return fr_typography(text) if lang == "fr" else text


CATEGORY_FR = {"Electronics": "Électronique", "Accessories": "Accessoires", "Home & Kitchen": "Maison et cuisine",
               "Fashion & Gear": "Mode et équipement"}


def complaint_label(code: str, lang: str = "en") -> str:
    return COMPLAINT_FR.get(code, code) if lang == "fr" else code


def category_label(code: str, lang: str = "en") -> str:
    return CATEGORY_FR.get(code, code) if lang == "fr" else code


def streamlit_lang() -> str:
    """Sidebar language switch shared by all pages (kept in session state, English by default)."""
    import streamlit as st
    codes = list(LANGS)
    current = st.session_state.get("lang", "en")
    choice = st.sidebar.selectbox(t("language", current), codes, index=codes.index(current), format_func=LANGS.get)
    st.session_state["lang"] = choice
    return choice
