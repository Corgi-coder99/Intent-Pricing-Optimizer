import re

import pandas as pd
import pytest

from src import i18n
from src.agent_decision import PricingDecisionAgent, money, number

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_ui_string_exists_in_english_and_french():
    for key, entry in i18n.STRINGS.items():
        assert set(entry) >= {"en", "fr"} and entry["en"].strip() and entry["fr"].strip(), key


def test_placeholders_match_between_languages():
    for key, entry in i18n.STRINGS.items():
        assert set(PLACEHOLDER.findall(entry["en"])) == set(PLACEHOLDER.findall(entry["fr"])), key


def test_unknown_key_and_language_fall_back_gracefully():
    assert i18n.t("no_such_key", "fr") == "no_such_key"
    assert i18n.t("title", "de") == i18n.t("title", "en")
    assert i18n.t("cc_countries", "fr", home="FR") == "Pays comparés à FR"


def test_english_ui_has_no_vietnamese_left():
    vn = re.compile(r"[ăđơưĂĐƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]")
    for key, entry in i18n.STRINGS.items():
        assert not vn.search(entry["en"]) and not vn.search(entry["fr"]), key


def test_money_and_number_formatting():
    assert money(1234.5, "en") == "€1,234.50"
    assert money(1234.5, "fr") == "1 234,50 €"
    assert number(41.25, "fr", 1) == "41,2" and number(41.25, "en", 1) == "41.2"


def curve(best_discount, best_price=100.0):
    return pd.DataFrame({"offered_price": [best_price, 90.0], "discount_pct": [best_discount, 10.0],
                         "conversion_prob": [0.5, 0.4], "expected_revenue_per_100_visits": [5000.0, 3600.0]})


@pytest.mark.parametrize("lang,expect", [("en", "Defensive Conversion Recovery"), ("fr", "Redressement défensif")])
def test_agent_speaks_the_requested_language_with_euro_prices(lang, expect):
    d = PricingDecisionAgent().recommend_action("Headphones", 125.0, curve(20.0), ["price_value"], 0.3, lang=lang)
    assert d["strategy_name"] == "Defensive Conversion Recovery"            # stable identifier in every language
    assert expect in d["strategy_label"]
    assert "€" in d["recommended_price"] and "$" not in d["executive_summary"]
    if lang == "fr":
        assert "remise" in d["executive_summary"] and "rapport qualité-prix" in d["executive_summary"]
        assert "\u00a0%" in d["executive_summary"] and " %" not in d["executive_summary"]   # non-breaking space before %
    else:
        assert "discount" in d["executive_summary"]


def test_default_language_is_english_and_unknown_language_falls_back():
    d = PricingDecisionAgent().recommend_action("X", 125.0, curve(20.0), [], 0.3, lang="xx")
    assert "discount" in d["executive_summary"]


def test_api_accepts_lang_and_rejects_unknown(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src import api
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("REQUIRE_API_KEY", raising=False)
    monkeypatch.setenv("AUDIT_LOG", str(tmp_path / "a.log"))
    with TestClient(api.app) as c:
        body = {"base_price": 100, "competitor_price": 100, "ai_sentiment_score": 0.3}
        fr = c.post("/v1/price/recommend", json={**body, "lang": "fr"})
        assert fr.status_code == 200 and "€" in fr.json()["decision"]["recommended_price"]
        assert c.post("/v1/price/recommend", json={**body, "lang": "xx"}).status_code == 422


def test_french_typography_uses_non_breaking_spaces():
    assert i18n.fr_typography("Sources : « x » 5 %") == "Sources\u00a0: «\u00a0x\u00a0» 5\u00a0%"
    assert i18n.t("cc_note_5", "fr").startswith("Sources\u00a0:")
    assert i18n.t("cc_note_5", "en").startswith("Sources: ")                    # English untouched


def test_english_agent_text_has_no_raw_complaint_codes():
    import pandas as pd
    from src.agent_decision import PricingDecisionAgent
    sim = pd.DataFrame({"offered_price": [90.0, 100.0], "discount_pct": [10.0, 0.0], "conversion_prob": [0.5, 0.4],
                        "expected_revenue_per_100_visits": [4500.0, 4000.0]})
    d = PricingDecisionAgent(use_llm=False).recommend_action("Widget", 100.0, sim, ["price_value", "shipping_packaging"], 0.4, lang="en")
    assert "price_value" not in d["executive_summary"] and "shipping_packaging" not in d["executive_summary"]
