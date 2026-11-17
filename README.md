# Intent-Driven Dynamic Pricing & Conversion Optimizer

AI (review NLP/RAG) + Data Science (LightGBM price elasticity) + E-commerce (pricing agent), với dashboard BI lọc theo ngành hàng, khu vực và đối thủ. Dữ liệu mô phỏng.

```bash
pip install -r requirements.txt
python -m src.train_pipeline
python -m pytest -v tests
streamlit run app/streamlit_app.py
```
Mockup giao diện enterprise: `docs/enterprise_dashboard_mockup.html`.

## REST API
```bash
uvicorn src.api:app --host 127.0.0.1 --port 8000   # docs tại /docs
```
Endpoints: `GET /health`, `POST /v1/review/analyze`, `POST /v1/price/simulate`, `POST /v1/price/recommend`.
Đặt `API_KEY` để bắt buộc header `X-API-Key`. Docker: `docker build -t pricing-api . && docker run -p 8000:8000 -e API_KEY=... pricing-api`.
