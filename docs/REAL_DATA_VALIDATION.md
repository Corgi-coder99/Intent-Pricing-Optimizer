# Validating the model with real data, and evaluating new sources

Produced with `python -m src.real_elasticity` (figures of 2026-11-17). Full output: `data/reports/real_elasticity.json` (not in git).

## 1. Price sensitivity: simulation vs real transactions
Real source: Online Retail II (Chen, D. 2012, UCI, DOI 10.24432/C5CG6D, **CC BY 4.0**), anonymised transactions of a UK online
retailer, 12/2009 to 12/2011, read (read-only) from the `clean_lines` table of the online-retail-insights project. Unit of observation: product x week.
Kept only: sales (no returns, no non-product codes, no price <= 0), products with ≥30 weeks, price variation (CV ≥ 5%, ≥3 price levels),
weeks with ≥2 invoices, and extreme prices relative to the median removed.

| Subject | Price elasticity of demand | Products | Usable? |
|---|---|---|---|
| All data | **−2.27** (95%: −2.34 to −2.19) | 1,709 | Yes |
| UK | −2.20 (95%: −2.28 to −2.13) | 1,640 | Yes |
| EU other than UK | −8.47 | **46** | **No**: too few products, lumpy wholesale orders |
| Simulation, revenue-weighted average | **−2.44** | 50,000 sessions | (comparison) |
| Simulation, mean / median | −3.02 / −1.71 | 50,000 sessions | (comparison) |

**Conclusion:** the OVERALL price sensitivity of the simulation (−2.4) is close to the observed real value (−2.3), so the simulated data is not recalibrated.
The estimator was first checked on synthetic data with known elasticity (it recovers −1.5 and −0.4; covered by tests).

**Not verified, still an assumption:** the differences by complaint type in the simulation (price-complaint customers −6.0; no complaint −1.3;
quality defect −3.1; shipping −2.4). The real data above has no reviews, so this part cannot be checked.

**Limits of the validation:** (a) this is an OBSERVED elasticity, not a causal one (promotions coincide with high-demand periods, tiered wholesale pricing);
(b) the products are gifts/homeware sold wholesale-and-retail in the UK, not food or electronics; (c) quantity demand differs from conversion probability
(the two are only comparable at the order-of-magnitude level).

## 2. Evaluation of new sources (process: licence, access, coverage, reliability, risk)
| Source | Decision | Reason |
|---|---|---|
| Online Retail II (UCI) | **Use** (validates Tier 2) | CC BY 4.0, anonymised, already available, has a country column |
| Open Prices, ECB, Eurostat | **Use** (loaded earlier) | Open, official APIs |
| Amazon Reviews Multi (has French/German) | **Reject** | Defunct; licence is non-commercial research only and bans use where a fee is received (including paid internships) |
| Mirrors of review datasets tagged Apache/CC0/CC BY | **Not used yet** | A tag on a mirror does not change the terms of the original data (Amazon); original licence could not be verified |
| Allociné (French movie reviews) | **Reject** | Not e-commerce |
| Webis-CLS-10 (book/DVD/music reviews) | **Reject** | Unclear licence, not the project's product categories |
| Women's E-Commerce Clothing Reviews (Kaggle, CC0 per the Kaggle page) | **Candidate, not used** | Needs a Kaggle account to download; the licence is only confirmed via an aggregator page. If you download it yourself, you can drop it into `data/landing/` after renaming columns to the `review` contract |
| Amazon (selling fees, International segment results) | **Not used** | Could not be verified at the primary source (see `docs/DATA_SOURCES.md`) |

**Tier 1 (review reading) is still not validated on real reviews.** The classifier only knows about 20 English keywords and has no negation
handling ("not overpriced" is scored like "overpriced"); treat it as an illustration until its accuracy is measured on real reviews with a clean licence.
The measuring tool exists: `python -m src.review_validation --file reviews.csv --text-col <col> --rating-col <col>` reports polarity accuracy,
a confusion matrix, coverage and a verdict, without ever printing review text (written by Codex, task T-051).
A bug caught by browser testing: the sentence "Packaging felt cheap and protective seal was broken" was mislabelled `quality_defect`
(it used the last matching category); it now picks the category with the most keyword hits. Its own test then exposed two more substring bugs
("fast" matched inside "faster", "late" inside "chocolate"); keywords are now matched as whole words and the ambiguous "fast" was dropped.
