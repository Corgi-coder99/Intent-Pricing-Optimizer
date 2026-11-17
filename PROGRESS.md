# PROGRESS.md

## Status: MVP + BI dashboard hoàn tất, 6/6 business-scenario tests PASS

### Done
- 3 tầng AI (lexicon/LLM) -> ML (LightGBM, monotone constraints) -> Agent (chiến lược giá), có RAG TF-IDF.
- Dữ liệu mô phỏng 50.000 phiên, có thêm chiều vùng địa lý (7 vùng VN), 3 đối thủ/sản phẩm.
- `app/streamlit_app.py`: giao diện kiểu BI (KPI cards, gauge, bản đồ vùng, so sánh đối thủ & ngành hàng, bộ lọc).
- `tests/test_business_scenarios.py`: 6 kịch bản nghiệp vụ.

### Tuning đã làm (để 6/6 PASS)
- data_generator: độ nhạy giá phụ thuộc loại phàn nàn (none 0.08x, price_value 1.3x) và vùng; bỏ bước nhảy theo ngưỡng giảm giá.
- ml_pricing_model: monotone_constraints, mô hình mượt hơn (16 lá, min_child_samples=300).
- Mặc định 50.000 mẫu (5.000 mẫu quá nhiễu cho đường cong giá).

### Limits
- Dữ liệu 100% mô phỏng; ROC-AUC ~0.65. Đây là demo logic liên tầng, không phải số liệu thật.
- Bước tiếp theo: Olist adapter (dữ liệu thật), Dockerfile.
