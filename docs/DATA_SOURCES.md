# Data sources and how far to trust them

| Data | Source | Status |
|---|---|---|
| Retail shelf prices by store and country (8 EU/UK countries) | Open Prices (Open Food Facts), ODbL licence, via API | Real, community-contributed, no accuracy guarantee. The last download stopped early (request cap); Belgium is incomplete |
| Exchange rates EUR/GBP... | ECB (data-api.ecb.europa.eu), read live on 2026-11-17 | Real. 1 GBP = 1.2004 EUR |
| Cost structure of one e-commerce platform | Zalando SE, consolidated results 2024 (https://corporate.zalando.com/en/node/10738), read live on 2026-11-17 | Real, verified at the primary source |
| Retail gross margin by country (7 EU countries + UK) | Eurostat, dataset `sbs_na_dt_r2`, indicators V12110 and V12130, fetched via the official API on 2026-11-17 (`src/connectors/eurostat_retail.py`) | Real, SECTOR-wide figures per country, year 2019 (UK: 2018). Margin % computed in code |
| VAT by country | `src/pipeline/reference/vat_rates.csv` | **Not verified** (entered from memory; check against an official source) |
| Branch unit costs | None (internal company data, not public) | Missing |

## Retail gross margin by country (Eurostat), supermarket/food group G47.11, turnover excluding VAT
France 21.02% · Germany 23.41% · Spain 23.94% · Italy 22.67% · Netherlands 27.33% · Belgium 37.74% · Ireland 34.68% · UK 31.81% (2018).
- These are sector-wide gross margins per country (turnover minus purchases of goods for resale), **not** the cost of any company or product.
- 2020 is excluded (the pandemic distorts it); the UK only goes up to 2018 because Eurostat stopped collecting it after Brexit, so the UK is a different year.
- Some countries swing strongly between years (e.g. Ireland, all retail: 20.3% in 2018, 37.0% in 2019); do not read small gaps between countries as real differences.
- Used as the anchor of the scenario (`scenario_anchor` column) and as a benchmark (`sector_gross_margin_pct` column).

## How the Zalando figures are used, and what they cannot do
2024 (EUR million): revenue 10,572.5; cost of sales 6,270.5; gross profit 4,302.0; fulfilment costs 2,418.4; marketing costs 979.2.
Ratios computed from these: gross margin 40.69%; fulfilment 22.87%; marketing 9.26%; about 8.55% of revenue left after both costs.

- These are aggregates of ONE fashion company. They do **not** give per-product or per-country cost, and do not represent food retail.
- The Zalando figures are only a fallback anchor when the home country's Eurostat figure is missing.
- **Hypothetical scenario** (`scenario_margin_pct` in the `market_margin` table): assume every branch buys at the same unit cost, and that
  this cost gives a margin equal to the home country's sector margin (France 21.02% from Eurostat, shown in `scenario_anchor`) when selling at the
  French market price; then what would the margin be when selling at another country's market price? The result is a **hypothetical** margin, not any company's real profit.
- With real branch costs (`branch_cost`), the `margin_at_market_pct` and `own_margin_pct` columns use real numbers and replace the scenario.

## Searched but NOT used
- **Amazon, International segment (2025 results):** only available from third-party aggregator pages; the primary press release
  returned HTTP 403, so it is unverified. Not put into the data.
- **Amazon selling fees in Europe:** no official fee table could be retrieved (only seller-forum posts and third-party blogs,
  mostly for the US marketplace). Not put into the data. To use them, take the fee schedule from each country's Seller Central.
- Real per-product or per-country cost data of a company selling on platforms: no trustworthy public source found.

## Attribution when publishing
"Contains information from Open Food Facts contributors, available under the ODbL." Zalando figures: Zalando SE, 2024 report. Sector figures: Eurostat (sbs_na_dt_r2).
