# Data attributions

The code is MIT-licensed (see LICENSE). The data it can fetch or use has its own terms:

| Data | Source | Terms |
|---|---|---|
| Shelf prices | Open Prices, Open Food Facts contributors | ODbL 1.0: "Contains information from Open Food Facts contributors, available under the ODbL." |
| Exchange rates | European Central Bank reference rates | ECB terms of use (free reuse with attribution) |
| Retail gross margins | Eurostat, dataset sbs_na_dt_r2 | Eurostat reuse policy (CC BY 4.0) |
| Real transactions (validation only) | Online Retail II, Chen, D. (2012), UCI Machine Learning Repository, DOI 10.24432/C5CG6D | CC BY 4.0 |
| Cost structure figures | Zalando SE annual results 2024 | Published company figures, cited with source URL in `src/pipeline/reference/published_benchmarks.csv` |

Downloaded data is never committed to this repository (`data/` is git-ignored). Simulated sessions are generated locally.
