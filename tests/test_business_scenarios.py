import numpy as np
import pandas as pd
import pytest

from src.agent_decision import PricingDecisionAgent
from src.ai_extractor import ReviewSignalExtractor
from src.config import MODEL_ARTIFACT_PATH, PROCESSED_DATASET_PATH
from src.ml_pricing_model import DynamicPricingModel
from src.rag_engine import ReviewRAGEngine
from src.train_pipeline import run_full_pipeline


@pytest.fixture(scope="module")
def system_components():
    """Ensures artifacts exist and loads all 3 tiers once for the test suite."""
    if not PROCESSED_DATASET_PATH.exists() or not MODEL_ARTIFACT_PATH.exists():
        run_full_pipeline(n_samples=50000, use_llm=False)

    df = pd.read_csv(PROCESSED_DATASET_PATH)
    extractor = ReviewSignalExtractor(use_llm=False)
    rag = ReviewRAGEngine(df)
    model = DynamicPricingModel()
    model.load()
    agent = PricingDecisionAgent(use_llm=False)
    return df, extractor, rag, model, agent


def test_case_1_quality_defect_crisis(system_components):
    """Case 1: Severe product defect reviews trigger low sentiment & defensive discount."""
    _, extractor, _, model, agent = system_components
    review = "Battery drains much faster than advertised after just two weeks. Button feels loose and broken."

    signals = extractor.analyze_single_review(review)
    assert signals["complaint_category"] == "quality_defect"
    assert signals["ai_sentiment_score"] < 0.35
    assert signals["pain_point_severity"] >= 0.70

    context = {
        "base_price": 149.99,
        "competitor_price": 145.00,
        "ai_sentiment_score": signals["ai_sentiment_score"],
        "pain_point_severity": signals["pain_point_severity"],
        "session_views": 5.0,
        "cart_additions_7d": 20.0,
    }
    sim_df = model.simulate_price_curve(context)
    decision = agent.recommend_action(
        product_name="ProSound ANC Wireless Headphones",
        base_price=149.99,
        sim_df=sim_df,
        top_complaints=[signals["complaint_category"]],
        current_sentiment=signals["ai_sentiment_score"],
    )

    assert decision["strategy_name"] == "Defensive Conversion Recovery"
    recommended_price = float(decision["recommended_price"].replace("€", ""))
    assert recommended_price < 149.99


def test_case_2_margin_expansion_opportunity(system_components):
    """Case 2: High sentiment + higher competitor price supports dynamic price markup."""
    _, extractor, _, model, agent = system_components
    review = "Build quality exceeds expectations and works seamlessly out of the box. Worth every penny!"

    signals = extractor.analyze_single_review(review)
    assert signals["complaint_category"] == "none"
    assert signals["ai_sentiment_score"] >= 0.80

    context = {
        "base_price": 119.00,
        "competitor_price": 145.00,  # Competitor is much more expensive
        "ai_sentiment_score": signals["ai_sentiment_score"],
        "pain_point_severity": signals["pain_point_severity"],
        "session_views": 6.0,
        "cart_additions_7d": 35.0,
    }
    sim_df = model.simulate_price_curve(context)
    decision = agent.recommend_action(
        product_name="ErgoFlow Mechanical Keyboard",
        base_price=119.00,
        sim_df=sim_df,
        top_complaints=[],
        current_sentiment=signals["ai_sentiment_score"],
    )

    assert decision["strategy_name"] == "Margin Expansion (Dynamic Markup)"
    recommended_price = float(decision["recommended_price"].replace("€", ""))
    assert recommended_price > 119.00


def test_case_3_price_value_friction(system_components):
    """Case 3: Overpriced product vs competitor detects price_value friction and lowers price."""
    _, extractor, _, model, _ = system_components
    review = "Good product overall, but honestly overpriced compared to competitors. Too expensive."

    signals = extractor.analyze_single_review(review)
    assert signals["complaint_category"] == "price_value"

    context = {
        "base_price": 99.00,
        "competitor_price": 79.00,
        "ai_sentiment_score": signals["ai_sentiment_score"],
        "pain_point_severity": signals["pain_point_severity"],
        "session_views": 8.0,
        "cart_additions_7d": 15.0,
    }
    sim_df = model.simulate_price_curve(context)
    best_row = sim_df.loc[sim_df["expected_revenue_per_100_visits"].idxmax()]
    assert best_row["offered_price"] < 99.00


def test_case_4_shipping_friction_rag_retrieval(system_components):
    """Case 4: Logistics complaints are accurately tagged and retrieved by RAG engine."""
    _, extractor, rag, _, _ = system_components
    review = "Arrived 5 days late and the outer box was badly crushed during transit."

    signals = extractor.analyze_single_review(review)
    assert signals["complaint_category"] == "shipping_packaging"

    rag_result = rag.retrieve_product_evidence(
        product_id="PRD-104", focus_query="late shipping crushed box", top_k=2
    )
    assert "evidence_quotes" in rag_result
    assert len(rag_result["evidence_quotes"]) > 0


def test_case_5_mixed_review_signal_weighting(system_components):
    """Case 5: Mixed reviews with negative defects/price must not be scored as positive."""
    _, extractor, _, _, _ = system_components
    mixed_review = "Sleek design and great look, but battery drains fast and it is overpriced."

    signals = extractor.analyze_single_review(mixed_review)
    assert signals["ai_sentiment_score"] < 0.50
    assert signals["pain_point_severity"] > 0.30


def test_case_6_simulation_mathematical_integrity(system_components):
    """Case 6: Verifies probability bounds and expected revenue formula across all price steps."""
    _, _, _, model, _ = system_components
    context = {
        "base_price": 100.00,
        "competitor_price": 100.00,
        "ai_sentiment_score": 0.55,
        "pain_point_severity": 0.25,
        "session_views": 4.0,
        "cart_additions_7d": 25.0,
    }
    sim_df = model.simulate_price_curve(context)

    assert len(sim_df) == 15
    assert not sim_df.isnull().any().any()
    assert ((sim_df["conversion_prob"] >= 0.0) & (sim_df["conversion_prob"] <= 1.0)).all()

    expected_calc = sim_df["offered_price"] * sim_df["conversion_prob"] * 100.0
    assert np.allclose(sim_df["expected_revenue_per_100_visits"], expected_calc, atol=0.05)

