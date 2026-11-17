# PROGRESS.md

## Status: working end-to-end; bilingual (EN/FR) dashboard; real EU/UK market data wired in

### Done
- 3 tiers: review signal extractor (offline lexicon or LLM) -> LightGBM price elasticity (monotone constraints, native text model) -> pricing agent (EN/FR), with TF-IDF RAG.
- Simulated data: 50,000 sessions in 7 French sales regions, 3 competitors per product, prices in EUR.
- Bronze/silver/gold pipeline: contracts, deduplication, quarantine, history, tombstones, retention, timeouts, locks (see `docs/DATA_PIPELINE.md`).
- Connectors: Open Prices (country-balanced), ECB exchange rates, Eurostat retail margins; all hardened (see `docs/SECURITY.md`).
- REST API with per-user keys, roles, audit log, rate limits; Docker image.
- Dashboard: main page + "Country comparison" page, English/French switch; verified in Chrome.
- Real-data validation of price elasticity (Online Retail II): real -2.27 vs simulated -2.44 (revenue-weighted).
- 160+ tests.

### Known limits
- The conversion model is trained on simulated data; review reading (Tier 1) is not validated on real reviews.
- No real branch cost data: margins in the country comparison are a hypothetical scenario. The VAT table is unverified.
- Few products have prices in several countries; only a small share of rows are "reliable" (≥2 stores, ≥3 observations).

### Next ideas
- Validate Tier 1 on a real review dataset with a clean licence (candidate: Women's E-Commerce Clothing Reviews, CC0 on Kaggle; download it yourself).
- Plug in real branch costs through the `branch_cost` contract (`python -m src.pipeline check` validates a file first).
- Push to GitHub (never commit `data/`, `.env` or API keys).
