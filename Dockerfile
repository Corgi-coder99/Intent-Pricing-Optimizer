FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser app ./app
RUN mkdir -p data && chown appuser:appuser data
USER appuser
# Huan luyen mo hinh khi build de container chay ngay
RUN python -m src.train_pipeline
# Bat buoc truyen API_KEY luc chay: docker run -e API_KEY=... ; thieu thi moi endpoint tra 503 (khong mo cong khai)
ENV REQUIRE_API_KEY=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)"
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
