# Security, multi-user use and external data sources

## Data source: Open Prices (Open Food Facts)
- Licence: ODbL. When publishing, credit: "Contains information from Open Food Facts contributors, available under the ODbL".
- Official public API only, no web scraping. No Amazon/Shopee/Lazada scraping.
- The API also returns `owner` (contributor username), `owner_comment` and `proof` (receipt image). The connector does NOT store these fields.
- **Why some countries (Ireland, Germany) were thin at first:** the API ignores country filters (on both `/prices` and `/locations`);
  it only filters by currency or `location_id`. A EUR scan only sees the latest records, dominated by France/Germany.
  Fix: scan the location list (~8,000), filter countries client-side, then fetch prices per `location_id` with a quota per
  country and per location (`--mode by-location`, the default). The UK has only ~2,000 records in the whole source.
- Allowed countries: EU27 + United Kingdom. Allowed currencies: EUR, GBP, SEK, DKK, PLN, CZK, HUF, RON, BGN.

## Connectors (src/connectors/)
HTTPS to one host and a fixed set of paths · TLS verified (certifi bundle) · redirects not followed · URLs found in data are never
fetched (anti-SSRF) · 5 MB per response, 20-60 s timeout, 1 request/second, bounded retries, a hard cap on total requests ·
JSON/CSV only · allow-listed fields · every value validated · text sanitised (control characters, CSV formulas, `|`) ·
generic User-Agent, no cookies/tokens/personal data · files written via tmp + rename · partial results are kept and flagged when a run stops early.

## Model (pickle removed)
The model is stored at `data/processed/lgbm_pricing.txt` in LightGBM's native text format. Loading it cannot run arbitrary code
(unlike `joblib/pickle`). Each model has a `model_version` (SHA-256 of the file) returned by `/health` and every prediction response.
The old `lgbm_pricing.pkl` is no longer loaded and can be deleted.

## Several users, several machines
- **One key per person:** `python -m src.auth create --name alice --role user` (the key is shown once; only its SHA-256 hash is stored).
  Role `user` calls predictions; `admin` also calls `/v1/admin/reload`. Disable with `python -m src.auth disable --id <id>`.
  The `API_KEY` environment variable is a shared admin key (use it for operations only).
- **A key store that ever held a key never falls back to open mode**: even if every key is disabled, or the key file is deleted or emptied
  (a marker file records that a key was created), nobody can authenticate until a new key is created. It never silently opens the API.
- **No clashes:** prediction endpoints write no shared state (they only read the model), so concurrent callers never overwrite each other.
  Rate limits are counted PER KEY (120/min), so one heavy user cannot starve the others; the per-IP limit is high (600/min)
  because an office usually shares one IP.
- **Same results:** the same `model_version` + the same input gives exactly the same output on every machine. Results differ only
  if machines use different models (each trained on different data). Use ONE shared API server or distribute the same model file.
- **Audit log:** `data/state/audit.log` (rotating, 5 MB x 3) records time, request_id, IP, which key, path and status;
  it NEVER records request content. Every response carries an `X-Request-ID` header for tracing.
- **Reverse proxy:** `X-Forwarded-For` is trusted only when `TRUST_PROXY=1` (the last entry added by the proxy, which must be a
  valid IP); by default the header is ignored, so spoofing it cannot evade the rate limit.
- **Running the pipeline from several machines on one shared network folder is NOT supported:** the run lock is an OS-level file lock
  (released automatically if the owning process dies; no stale-lock timeout), which is unreliable on some network shares. Give each machine its own `data/`, or run the pipeline on one machine only.

## API
Constant-time key comparison · `REQUIRE_API_KEY=1` (set in Docker) answers 503 when no key is configured, never open ·
wrong-key throttling (after 10 wrong keys/minute from one IP, further WRONG keys get 429; a valid key is never locked out, so an attacker
sharing an IP or reverse proxy cannot deny service to real users) · 64 KB body limit, and requests without a Content-Length are refused (411) · `nosniff`, `no-store`, `X-Frame-Options` headers ·
Pydantic input validation · unhandled server errors still return the security headers and write an audit record · Docker runs as a non-root user.

## Dashboard
Streamlit listens on `127.0.0.1` only (`.streamlit/config.toml`); its default is every network interface and the dashboard has no
authentication. Open `http://127.0.0.1:8501` (Chrome may resolve `localhost` to IPv6 and fail to connect).

## Poisoned or wrong community data
The `competitor_prices` table uses robust statistics: points beyond 5 MAD are excluded from min/median/max and counted in
`n_outliers`; each group has a `confidence` (`ok` with ≥2 stores and ≥3 observations, otherwise `low`). This blocks clearly extreme
values; it does not stop a careful attacker who sets prices close to reality.

## Scanned
No keys/passwords in the repo · `.env`, `data/`, `*.pkl` are outside git · no eval/exec/pickle/shell · `pip-audit` clean at scan time
and re-run weekly via `.github/workflows/security.yml` (only runs once the repo is pushed to GitHub).

## Remaining risks (not fixable in code)
- HTTPS, a WAF and rate limiting across several service copies must be done at the infrastructure level (reverse proxy such as
  nginx/Caddy, API gateway). The rate-limit counters live in each process's memory and are not shared between copies.
- API keys are only safe over HTTPS; over plain HTTP they can be sniffed on the wire.
- Anyone who can read `data/state/api_keys.json` sees only hashes, but anyone who can write to it can create keys: restrict write access.
- Open Prices is community-contributed data with no accuracy guarantee.
