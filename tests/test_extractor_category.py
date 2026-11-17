import pytest

from src.ai_extractor import ReviewSignalExtractor

EXT = ReviewSignalExtractor(use_llm=False)


@pytest.mark.parametrize("text,expected", [
    ("Packaging felt cheap and protective seal was broken on arrival.", "shipping_packaging"),   # lỗi thấy trên dashboard
    ("Arrived 5 days late and the outer box was badly crushed during transit.", "shipping_packaging"),
    ("Battery drains much faster than advertised after just two weeks.", "quality_defect"),
    ("Button feels loose and materials feel flimsier than previous versions.", "quality_defect"),
    ("Too expensive for what you get. Wait for a promo code before buying.", "price_value"),
    ("Build quality exceeds expectations and works seamlessly out of the box.", "none"),
])
def test_template_reviews_get_the_right_category(text, expected):
    assert EXT.analyze_single_review(text)["complaint_category"] == expected


def test_category_is_the_one_with_most_keyword_hits_not_the_last_match():
    # 1 từ khoá chất lượng (broken) nhưng 2 từ khoá vận chuyển (packaging, seal)
    assert EXT.analyze_single_review("packaging seal broken")["complaint_category"] == "shipping_packaging"
    # 3 từ khoá giá vs 1 từ khoá chất lượng
    assert EXT.analyze_single_review("overpriced expensive price but drains")["complaint_category"] == "price_value"
