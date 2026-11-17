import random
from typing import List, Tuple
import numpy as np
import pandas as pd
from src.config import RANDOM_SEED, RAW_DATASET_PATH

# competitor_price = main competitor's price (used by the model); `competitors` = 3 competitors for visual comparison.
PRODUCT_CATALOG = [
    {
        "product_id": "PRD-101",
        "product_name": "ProSound ANC Wireless Headphones",
        "category": "Electronics",
        "base_price": 149.99,
        "competitor_price": 145.00,
        "competitors": {"SonicWave": 145.00, "AudioMax": 159.00, "BeatLab": 139.00},
    },
    {
        "product_id": "PRD-102",
        "product_name": "ErgoFlow Mechanical Keyboard",
        "category": "Accessories",
        "base_price": 119.00,
        "competitor_price": 125.00,
        "competitors": {"KeyCraft": 125.00, "TypePro": 135.00, "ClickZone": 115.00},
    },
    {
        "product_id": "PRD-103",
        "product_name": "AeroBrew Smart Pour-Over Coffee Maker",
        "category": "Home & Kitchen",
        "base_price": 89.50,
        "competitor_price": 84.99,
        "competitors": {"BrewMate": 84.99, "CafeNova": 92.00, "DripKing": 79.00},
    },
    {
        "product_id": "PRD-104",
        "product_name": "TrailBlaze Waterproof Commuter Backpack",
        "category": "Fashion & Gear",
        "base_price": 75.00,
        "competitor_price": 79.00,
        "competitors": {"UrbanPack": 79.00, "RoamGear": 85.00, "DryBag Co": 72.00},
    },
    {
        "product_id": "PRD-105",
        "product_name": "Lumina 4K USB-C Webcam",
        "category": "Electronics",
        "base_price": 129.00,
        "competitor_price": 119.00,
        "competitors": {"ClearCam": 119.00, "StreamEye": 135.00, "OptiView": 109.00},
    },
]

# French sales regions (simulated). price_sens: regional price sensitivity; demand: relative market size.
REGIONS = [
    {"region": "Lyon", "lat": 45.76, "lon": 4.84, "price_sens": 1.00, "demand": 0.22},
    {"region": "Paris", "lat": 48.86, "lon": 2.35, "price_sens": 0.90, "demand": 0.30},
    {"region": "Marseille", "lat": 43.30, "lon": 5.37, "price_sens": 1.15, "demand": 0.12},
    {"region": "Lille", "lat": 50.63, "lon": 3.06, "price_sens": 1.10, "demand": 0.10},
    {"region": "Toulouse", "lat": 43.60, "lon": 1.44, "price_sens": 1.25, "demand": 0.10},
    {"region": "Bordeaux", "lat": 44.84, "lon": -0.58, "price_sens": 1.05, "demand": 0.08},
    {"region": "Nantes", "lat": 47.22, "lon": -1.55, "price_sens": 1.20, "demand": 0.08},
]

POSITIVE_REVIEWS = [
    "Build quality exceeds expectations and works seamlessly out of the box.",
    "Worth every penny! Fast shipping and great performance compared to pricier brands.",
    "Super reliable, sleek design, and customer support was very responsive.",
    "Bought this on a slight discount and it has been a daily essential since day one.",
]

NEGATIVE_REVIEWS_BY_ISSUE = {
    "price_value": [
        "Good product overall, but honestly overpriced compared to competitors.",
        "Too expensive for what you get. Wait for a promo code before buying.",
        "Nice features, yet the price tag feels hard to justify right now.",
    ],
    "shipping_packaging": [
        "Arrived 5 days late and the outer box was badly crushed during transit.",
        "Shipping took forever and tracking updates stopped for a week.",
        "Packaging felt cheap and protective seal was broken on arrival.",
    ],
    "quality_defect": [
        "Battery drains much faster than advertised after just two weeks.",
        "Stopped working intermittently; feels like a quality control issue.",
        "Button feels loose and materials feel flimsier than previous versions.",
    ],
}


def _generate_review_batch(
    latent_quality: float, price_ratio: float, rng: random.Random
) -> Tuple[str, str]:
    """Generates a realistic customer review and true issue label based on latent product state."""
    if latent_quality > 0.65 and price_ratio <= 1.15:
        return rng.choice(POSITIVE_REVIEWS), "none"

    if price_ratio > 1.08:
        issue = "price_value"
    elif latent_quality < 0.40:
        issue = rng.choice(["quality_defect", "shipping_packaging"])
    else:
        issue = rng.choice(["price_value", "shipping_packaging", "none"])

    if issue == "none":
        return rng.choice(POSITIVE_REVIEWS), "none"
    return rng.choice(NEGATIVE_REVIEWS_BY_ISSUE[issue]), issue


def generate_ecommerce_dataset(
    n_samples: int = 10000, save: bool = True
) -> pd.DataFrame:
    """Generates synthetic e-commerce session data (pricing, behaviour, region, reviews)
    with realistic causal conversion dynamics."""
    np.random.seed(RANDOM_SEED)
    rng = random.Random(RANDOM_SEED)

    region_weights = [r["demand"] for r in REGIONS]
    records: List[dict] = []

    for i in range(n_samples):
        prod = rng.choice(PRODUCT_CATALOG)
        reg = rng.choices(REGIONS, weights=region_weights, k=1)[0]
        base_price = prod["base_price"]
        comp_price = prod["competitor_price"]

        discount_pct = float(np.round(np.random.uniform(-15.0, 20.0), 2))
        offered_price = round(base_price * (1 - discount_pct / 100.0), 2)
        comp_ratio = round(offered_price / comp_price, 3)

        latent_quality = float(np.clip(np.random.normal(0.62, 0.22), 0.1, 0.98))
        review_text, true_issue = _generate_review_batch(latent_quality, comp_ratio, rng)

        session_views = int(np.random.poisson(lam=4) + 1)
        cart_additions_7d = int(np.random.poisson(lam=28))

        # Customers with no complaint are less price-sensitive (Margin Expansion); price-complaint customers are more sensitive
        issue_sens = {"none": 0.08, "price_value": 1.3}.get(true_issue, 1.0)
        sens = reg["price_sens"] * issue_sens
        logit = (
            -1.1
            + 0.045 * sens * discount_pct
            - 2.2 * sens * (comp_ratio - 1.0)
            + 1.85 * (latent_quality - 0.5)
            + 0.08 * min(session_views, 8)
            + 0.005 * cart_additions_7d
        )
        # Quality defects lower conversion smoothly (no step change at a discount threshold)
        if true_issue == "quality_defect":
            logit -= 0.5

        prob = 1.0 / (1.0 + np.exp(-logit))
        converted = int(np.random.binomial(1, prob))

        records.append({
            "session_id": f"SES-{100000 + i}",
            "product_id": prod["product_id"],
            "product_name": prod["product_name"],
            "category": prod["category"],
            "region": reg["region"],
            "lat": reg["lat"],
            "lon": reg["lon"],
            "base_price": base_price,
            "competitor_price": comp_price,
            "offered_price": offered_price,
            "discount_pct": discount_pct,
            "competitor_price_ratio": comp_ratio,
            "session_views": session_views,
            "cart_additions_7d": cart_additions_7d,
            "recent_review_text": review_text,
            "converted": converted,
        })

    df = pd.DataFrame(records)
    if save:
        df.to_csv(RAW_DATASET_PATH, index=False)
    return df


if __name__ == "__main__":
    generated_df = generate_ecommerce_dataset()
    print(f"Generated {len(generated_df)} rows. Conversion rate: {generated_df['converted'].mean():.2%}")
