# Intent-Driven Dynamic Pricing & Conversion Optimizer

AI (review NLP/RAG) + Data Science (LightGBM price elasticity) + E-commerce (pricing agent), với dashboard BI lọc theo ngành hàng, khu vực và đối thủ. Dữ liệu mô phỏng.

```bash
pip install -r requirements.txt
python -m src.train_pipeline
python -m pytest -v tests
python -m streamlit run app/streamlit_app.py
```
Mockup giao diện enterprise: `docs/enterprise_dashboard_mockup.html`.

## REST API
```bash
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000   # chạy trong thư mục dự án; docs tại /docs
```
Endpoints: `GET /health`, `POST /v1/review/analyze`, `POST /v1/price/simulate`, `POST /v1/price/recommend`.
Đặt `API_KEY` để bắt buộc header `X-API-Key`. Docker: `docker build -t pricing-api . && docker run -p 8000:8000 -e API_KEY=... pricing-api`.

## Dữ liệu thật (EU/UK) và bảo mật
```bash
python -m src.connectors.openprices --currencies GBP EUR --countries GB FR DE IE --max-records 5000
python -m src.pipeline run
```
Nguồn: Open Prices, Open Food Facts contributors, giấy phép ODbL. Xem `docs/SECURITY.md` và `docs/DATA_PIPELINE.md`.

## Dashboard
```bash
python -m streamlit run app/streamlit_app.py
```
Trang chính: giá, chuyển đổi, đề xuất của agent. Trang "So sanh quoc gia" (thanh bên): so sánh giá và biên giả định giữa các nước EU/UK.
Trang này cần dữ liệu từ `python -m src.connectors.openprices`, `python -m src.connectors.ecb_fx` và `python -m src.pipeline run`.

Dashboard chỉ lắng nghe ở `127.0.0.1` (cấu hình trong `.streamlit/config.toml`); hãy mở `http://127.0.0.1:8501`.
Mặc định của Streamlit là mở trên mọi giao diện mạng và dashboard này không có xác thực, nên đừng bỏ cấu hình đó.
