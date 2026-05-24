# SearchMySanctions

A unified interface for screening individuals, entities, and crypto wallets against 40+ global sanctions, watchlist, and exclusion datasets.

## Tech Stack

| Layer | Technology |
|---|---|
| Sanctions data | [OpenSanctions](https://www.opensanctions.org/) — live dataset index + entity records |
| Population data | [US Census Bureau ACS API](https://www.census.gov/data/developers/data-sets/acs-1year.html) — state population for Medicaid offense rate charts |
| Provider data | [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api-page) — provider lookup for Medicaid exclusion cross-reference |
| API | Flask 3 + Blueprints |
| Cache | L1 in-memory dict → L2 Redis (Upstash) → L3 origin fetch |
| Persistence | Postgres via SQLAlchemy Core (notes + address search history) |
| Frontend | Vanilla JS + D3.js v7 (charts) |
| Templates | Jinja2 |
| Data processing | pandas, pgeocode, numpy |
| WSGI server | Gunicorn (gthread, 1 worker / 4 threads) |
| Hosting | Fly.io (auto-TLS at edge, machines + Upstash Redis + Fly Postgres) |

## Views

| View | Description |
|---|---|
| **Browse Datasets** | Full OpenSanctions catalog — filter by tag, country, type |
| **Visual Statistics** | Charts: publisher countries, cyber records, SDN crypto wallets, US population |
| **Cyber & Crypto** | Wallet screening against OFAC SDN, FBI Lazarus, ransomware lists |
| **PEP** | Politically Exposed Persons — split by country, dataset cards |
| **Medicaid Exclusions** | HHS OIG excluded providers — by state, sector, city, year |
| **Entity Search** | Cross-list fuzzy name/ID search |
| **Search by Country** | All sanctioned entities by nationality or jurisdiction |
| **Tags** | Dataset catalog grouped by topic tag |

## Cache Architecture

```
Request
  └── L1 (in-memory dict)  — microsecond, process lifetime
        └── L2 (Redis)     — millisecond, shared across machines, configurable TTL
              └── L3 (origin network fetch) — seconds, written back to L1+L2
```

On startup, a background thread promotes all valid L2 entity rows into L1 without making any network requests.

## Local Development

```bash
# 1. Bring up Redis + Postgres
docker compose up -d

# 2. Set env vars (see settings.py for the full list)
export REDIS_URL=redis://localhost:6379/0
export DATABASE_URL=postgresql+psycopg://app:app@localhost:5433/all_sanctions
export CENSUS_API_KEY=... ETHERSCAN_API_KEY=... SECRET_KEY=...

# 3. Install deps + run
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
python app.py    # dev server on :5001
# or: gunicorn -c gunicorn.conf.py app:app    # prod-like
```

## Testing

```bash
pytest tests/    # 225 tests, ~12s — uses fakeredis + temp SQLite, no Docker required
```

## Deployment

Push to `main` triggers GitHub Actions: runs the test suite, then `flyctl deploy --remote-only` if green. Manual deploy:

```bash
flyctl deploy
```
