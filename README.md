# Early-Stage European VC Startup Sourcing Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-2.5.0-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-40%20passed-brightgreen.svg)]()
[![Zero-Fake-Data](https://img.shields.io/badge/policy-Zero--Fake--Data-red.svg)]()
[![Track](https://img.shields.io/badge/MAIN%20Summit%202026-HTGF%20Track-orange.svg)]()

> **MAIN Summit 2026 — Track: High-Tech Gründerfonds (HTGF)**  
> **Geographic Focus:** ~70% Germany/DACH, Secondary Europe  
> **Investment Mandate:** Pre-Seed & Seed DeepTech, Artificial Intelligence, Industrial Technologies, Life Sciences

---

## 🏛️ System Architecture

The engine continuously uncovers high-potential, undiscovered European DeepTech spin-offs before they appear on institutional databases (Crunchbase, PitchBook). It screens out noisy agencies and hobby projects via 5 deterministic Kill-Gates, deeply enriches surviving founders with OpenAI live web search, scores companies against the HTGF IV investment mandate, runs specialized ML ranking models, and serves an interactive full-stack app (`first-signal/`) directly from FastAPI.

```
                                              [ 9 LIVE INGESTION SOURCES ]
  +----------------+  +----------------+  +----------------+  +----------------+  +----------------+
  |    ETH / RWTH  |  |    OpenAlex    |  |  Hugging Face  |  |  Luma Events   |  | CORDIS ERC-PoC |
  | University Labs|  | European Papers|  | Hub Org Models|  | Tech Demo-Days |  | Horizon Europe |
  +--------+-------+  +--------+-------+  +--------+-------+  +--------+-------+  +--------+-------+
           |                   |                   |                   |                   |
           +-------------------+---------+---------+-------------------+-------------------+
                                         |
  +----------------+  +------------------+-------------+  +----------------+
  |  European YC   |  |        BayStartUP Awards       |  |  TUM Venture   |  | European GitHub|
  | Recent Cohorts |  | Munich, Nordbayern, Schwaben   |  | Labs Spin-offs |  | Verified OSS   |
  +--------+-------+  +------------------+-------------+  +--------+-------+  +--------+-------+
           |                             |                         |                   |
           +-----------------------------+-------------------------+-------------------+
                                         |
                                         v
                           [ SQLite Ingestion & Cache ]
                              (data/scout.db & HTTP)
                                         |
                                         v
                      [ STAGE 3: IMPRESSUM & GITHUB VERIFIER ]
                      - German § 5 DDG Impressum Scraping
                      - GitHub Star & Fork-Ratio Verification
                                         |
                                         v
                      [ STAGE 4: DETERMINISTIC KILL-GATES ($0.00) ]
                      - Gate 1: Hobby / Event Communities Filter
                      - Gate 2: IT Consultancies & Agencies Filter
                      - Gate 3: Legacy Corporates & Age Filter (<= 3 yrs)
                      - Gate 4: GitHub Fake-Star Anomaly Filter
                      - Gate 5: Funding Blocklist (Scaleups, YC <= 2024)
                                         |
                                         v
                      [ STAGE 5: ASYNC FOUNDER ENRICHMENT ]
                      - OpenAI Web Search (GPT-4o-mini + Pydantic)
                      - Real Founder Pedigree (Doctorates, Prior Exits)
                      - HTGF IV LP Strategic Synergy Alignment
                      - Budget Circuit-Breaker ($12.00 hard cap)
                                         |
                                         v
                      [ STAGE 6: POST-ENRICHMENT AGE FILTER ]
                      - Eliminates stealth scaleups / old rebrands
                                         |
                                         v
                      [ STAGE 7: 5-PILLAR SCORING + ML BLEND ]
                      - Team Pedigree (32 pts)
                      - Commercial Intent (25 pts)
                      - Technical Moat (18 pts)
                      - Multi-Signal Convergence (15 pts)
                      - HTGF Mandate Fit (10 pts)
                      - 50/50 Blended Ranking: 0.5 * det + 0.5 * ml
                                         |
            +----------------------------+----------------------------+
            |                                                         |
            v                                                         v
 [ ML Contract CSV Export ]                                 [ Frontend Provenance Feed ]
 data/live_candidates_for_ml.csv                            data/wave2_frontend_feed.json
  (36 columns per features.md)                               (conversion funnel & audit)
            |                                                         |
            +----------------------------+----------------------------+
                                         |
                                         v
                             [ FastAPI REST Service ]
                            (http://localhost:8000)
                                         |
                      +------------------+------------------+
                      |                  |                  |
                      v                  v                  v
           [ FounderHunt Web UI ]  [ Interactive Docs ]  [ n8n Automation Dispatch ]
           (first-signal client)   (Swagger at /docs)    (Lusha & Partner Alerting)
```

---

## 🛡️ The 5 Deterministic Kill-Gates ($0.00 LLM Spend)

Before spending a single LLM token, all candidates must pass five zero-cost gatekeeper filters:

1. **Gate 1 — Hobby / Community / Meetup Host Filter:** Screens out student hackathon groups, meetup hosts, and community associations (`lu.ma/berlin-tech-meetup`).
2. **Gate 2 — IT Consultancy & Agency Filter:** Rejects software development agencies, marketing firms, and custom IT shops lacking proprietary scientific IP or research patents.
3. **Gate 3 — Legacy Corporate & Old Age Filter:** Excludes established enterprises older than 3 years (incorporation `< 2023`) and legacy conglomerates.
4. **Gate 4 — Fake GitHub Star Anomaly Filter:** Flags and rejects bot-farmed repositories where stargazer count $\ge 100$ but fork-to-star ratio $< 0.03$.
5. **Gate 5 — Already-Funded Scaleups & Historical YC Filter:** Cross-checks European tech funding feeds and 4,900+ historical YC companies ($\le 2024$) to eliminate growth-stage rounds that fall outside HTGF Seed/Pre-Seed tickets.

---

## 🎯 Transparent Multi-Pillar Scoring Engine (0–100 Points)

Every surviving lead is evaluated across five explainable dimensions with clear score catalysts and clickable evidence citations:

| Scoring Pillar | Weight | Key Evaluation Factors |
| :--- | :---: | :--- |
| **1. Team Pedigree** | **32 pts** | Verified doctorates (PhD), university lab researchers, technical co-founder / CTO presence, serial founders with prior exits. |
| **2. Commercial Intent** | **25 pts** | German Commercial Register (`HRB`) registration, verified legal form (`GmbH`/`AG`/`UG`), named Managing Directors (*Geschäftsführer*), and customer pilot trials. |
| **3. Technical Moat** | **18 pts** | Verified university spin-off origin (ETH Zurich, RWTH Aachen, TUM), proprietary research patents, active open-source traction with organic fork velocity. |
| **4. Multi-Signal Convergence** | **15 pts** | Candidate appearing across independent signal sources (e.g., CORDIS ERC Grant + GitHub open weights + university awards). |
| **5. HTGF Mandate Fit** | **10 pts** | Clear vertical alignment (`Industrial & Climate`, `Digital Tech & AI`, `Life Sciences & Chemistry`), DACH geography boost, and strategic synergy with **HTGF IV LP investors** (Robert Bosch, Evonik, SAP, Altana, etc.). |

### Machine Learning Ranking & Fact-Grounded Investment Memos
The ML layer (`backend/ml/`) integrates two specialized logistic regression models trained on European accelerator cohorts:
- **`model_acc`**: Optimized for accelerator-backed startups with network effects and founder credentials.
- **`model_noacc`**: Calibrated for organic deeptech university spin-offs (ETH Zurich, RWTH Aachen, Fraunhofer, Max Planck).

#### 50/50 Blended Ranking
When predictions are generated in `data/ml_predictions.csv`, the backend blends deterministic signal rigor with statistical pedigree scoring:
$$\text{blended\_score} = \text{round}(0.5 \times \text{deterministic\_score} + 0.5 \times \text{ml\_score}, 1)$$

#### Fact-Grounded LLM Memo Agent (`backend/ml/memo_agent.py`)
Generates 1-page structured investment briefs directly from verified facts in the provenance feed:
- **Zero Hallucination:** Only cites URLs and claims present verbatim in the candidate JSON payload.
- **Explainability:** Deconstructs positive/negative model drivers, HTGF LP synergies, devil's advocate risks, and recommends a specific HTGF sector team with a concrete founder question.
- **Offline Cached:** 15 Top Analyst memos are pre-generated and cached in `data/memo_cache/*.json` for instant sub-millisecond retrieval.

---

## 💻 Full-Stack Web Application (`first-signal/`)

The FounderHunt frontend is a clean, responsive single-page application served directly by FastAPI at `/`:
- **Live Leaderboard:** Filterable ranked ventures with blended scores, earliness badges (*Pre-Crunchbase / Undiscovered*), founder badges, and LP synergy tags.
- **European Scouting Events:** Over 42 upcoming demo-days, university pitch competitions, and founder meetups across 10+ European hubs (Berlin, Munich, Zurich, London, Paris, Amsterdam, Vienna, Copenhagen, Stockholm).
- **Interactive Dossiers:** Clickable venture deep dives featuring founder GitHub repositories, Impressum disclosures, and 1-page Analyst Investment Memos.
- **Operator Sourcing Console:** Interactive on-demand live research and automated CRM dispatch gated by `FOUNDERHUNT_WRITE_TOKEN`.

---

## 🔒 Operator Security & Write Token

Administrative actions are protected via bearer token authentication (`FOUNDERHUNT_WRITE_TOKEN` in `.env`):
- `POST /api/enrich/live`: Live web evaluation of any submitted European domain.
- `POST /api/ml/score`: Running ML ranking models and generating new LLM memos.
- `POST /api/workspace`: Saving shortlist decisions and watchlists.
- `POST /api/dispatch/n8n/{entity_id}`: Triggering n8n enrichment workflows.

Pass the token as `Authorization: Bearer <FOUNDERHUNT_WRITE_TOKEN>` in API calls or enter it in the UI Settings modal.

---

## 🐳 Docker Deployment (One-Command Launch)

The application packages compact demo data (~1.7 MB total: SQLite DB, ML predictions, EU events, and 15 pre-rendered analyst memos) directly into a non-root production container:

```bash
# Build the Docker image
docker build -t founderhunt .

# Run the container (bind to port 8000)
docker run -p 8000:8000 founderhunt
```

Visit [http://localhost:8000](http://localhost:8000) — launches immediately with all 63 ventures, 42 EU scouting events, ML rankings, and 15 Analyst briefs out-of-the-box!

---

## 💰 Cost Tracking, Safety & Zero-Fake-Data Policy

- **Strict Zero-Fake-Data Policy:** The engine never fabricates founder names, never invents LinkedIn URLs, and never synthesizes legal records. Missing fields remain explicit `null` or `0`.
- **Budget Circuit-Breaker:** Live OpenAI web search calls are tracked in real-time. If run spend reaches `max_budget_usd` (default `$2.00`), calls halt gracefully.
- **Append-Only Audit Ledger:** Every live API interaction logs tokens, web searches, and estimated costs into gitignored `data/cost_ledger.jsonl`.
- **Protected Cache Mode:** The `--clear-cache` flag requires explicit `--confirm-clear-cache` confirmation so cached intelligence is never accidentally wiped.

---

## ⚡ Quickstart Guide

### 1. Environment Setup

```bash
# Clone the repository
git clone https://github.com/VBortsov/TeamCJ_OpenAIMAINHackaton.git
cd TeamCJ_OpenAIMAINHackaton

# Create and activate virtual environment
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt
```

### 2. Configure Credentials

```bash
cp .env.example .env
```
Populate `.env` with your API keys:
- `OPENAI_API_KEY`: Required for live web-search enrichment (runs on Top leads).
- `FOUNDERHUNT_WRITE_TOKEN`: Operator security token for running research and generating memos.
- `GITHUB_TOKEN`: Optional (boosts GitHub API limit from 60/hr to 5,000/hr).
- `N8N_WEBHOOK_URL`: Optional (for automated CRM dispatch and Slack alerts).
- Optional free keys: `HF_TOKEN`, `OPENALEX_API_KEY`.

---

## 🏃 Running the Pipeline

### Mode A: Demo Rebuild ($0.00 — Cache-Only, No API Spend)
Re-evaluates and exports the entire leaderboard using verified cached intelligence:
```bash
python -m backend.run_wave2 --skip-ingestion --cache-only --top-k 200 --refresh-events
```

### Mode B: Live Discovery Run (With Ingestion & Budget Cap)
Runs full live multi-source ingestion, Impressum scraping, and web-search founder enrichment protected by a hard budget cap:
```bash
python -m backend.run_wave2 --limit 30 --top-k 200 --max-budget-usd 2.00
```

### Mode C: ML Scoring & Analyst Memo Generation
Run the dual-model ML ranking engine and generate structured investment memos:
```bash
python -m backend.ml.score_live
```

---

## 🚀 Running the FastAPI Server

Launch the full-stack server (serving both REST API and FounderHunt UI at `/`):
```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
```

- **FounderHunt Web UI:** [http://localhost:8000/](http://localhost:8000/)
- **Interactive Swagger Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health & Spend Monitor:** `GET http://localhost:8000/api/health`
- **Provenance Dashboard Feed:** `GET http://localhost:8000/api/dashboard`
- **European Scouting Events:** `GET http://localhost:8000/api/events`
- **Filterable Leads:** `GET http://localhost:8000/api/leads?country=DE&min_score=60`
- **1-Page Investment Memo:** `GET http://localhost:8000/api/leads/{entity_id}`
- **Fact-Grounded LLM Memo:** `GET http://localhost:8000/api/leads/{entity_id}/ml-memo`
- **Run ML Scoring & Generate Memos:** `POST http://localhost:8000/api/ml/score`
- **Founder GitHub Technical Record:** `GET http://localhost:8000/api/founders/github/{username}`
- **On-Demand Live URL Evaluation:** `POST http://localhost:8000/api/enrich/live`
- **n8n Webhook Dispatch:** `POST http://localhost:8000/api/dispatch/n8n/{entity_id}`

See [docs/API_CONTRACT.md](docs/API_CONTRACT.md) for complete endpoint schemas and ML handoff specifications.

---

## 🧪 Automated Test Suite

Run the full automated test suite (37 unit tests verifying Kill-Gates, Impressum parsing, Lead Scorer, ML models & memos, Cost Tracker circuit breaker, and FastAPI endpoints):
```bash
pytest -v
```

---

## 📁 Repository Structure

```
TeamCJ_OpenAIMAINHackaton/
├── backend/
│   ├── api/
│   │   └── main.py                     # FastAPI REST server, UI static mount & endpoints
│   ├── config.py                       # App settings, platform domains, and LP rosters
│   ├── db/
│   │   ├── repository.py               # SQLite storage, UUIDs, URL & city normalizers
│   │   └── schema.sql                  # Database schema (entities, signals, founders)
│   ├── enrichment/
│   │   ├── cost_tracker.py             # Budget circuit-breaker & cost ledger
│   │   ├── github_verifier.py          # Anti-fake-star & founder profile verification
│   │   ├── import_founder_links.py     # Offline founder profiles & social links merger
│   │   ├── impressum.py                # § 5 DDG German legal disclosures scraper
│   │   └── openai_enricher.py          # Live web-search pedigree & LP synergy enricher
│   ├── export/
│   │   └── ml_exporter.py              # ML contract CSV exporter (features.md)
│   ├── ingestion/
│   │   ├── adapters/                   # 6 Signal adapters (ETH/RWTH, OpenAlex, HF, Luma, CORDIS, YC)
│   │   ├── base.py                     # BaseSignalAdapter & RawCandidate contracts
│   │   └── http_client.py              # Resilient cached HTTP client with exponential backoff
│   ├── ml/                             # Machine learning ranking & memo subsystem
│   │   ├── memo_agent.py               # Fact-grounded 1-page investment memo agent
│   │   ├── score_live.py               # Standalone ML batch scoring script
│   │   ├── scoring.py                  # Model loader, feature extraction & inference
│   │   └── service.py                  # ML predictions service layer
│   ├── requirements.txt                # Python dependencies
│   ├── run_wave2.py                    # End-to-end pipeline runner
│   └── scoring/
│       ├── kill_gates.py               # 5 Deterministic zero-LLM Kill-Gates
│       └── scorer.py                   # 5-Pillar transparent lead scoring
├── first-signal/                       # FounderHunt interactive web UI (SPA)
│   ├── app.js                          # State management, filtering, dossiers, charts
│   ├── index.html                      # Semantic layout, accessible views, modals
│   └── styles.css                      # Modern dark/light responsive styling
├── data/                               # Bundled lightweight demo data (~1.7 MB total)
│   ├── events_feed.json                # 42 European scouting events across 10+ cities
│   ├── live_candidates_for_ml.csv      # ML inference handoff contract (36 columns)
│   ├── llm_cache/                      # Verified founder enrichment cache JSONs
│   ├── memo_cache/                     # Top-15 Analyst Investment Memo JSONs
│   ├── ml_predictions.csv              # Machine learning model predictions & percentiles
│   ├── scout.db                        # Primary SQLite database (63 ventures, signals)
│   └── wave2_frontend_feed.json        # Frontend provenance feed snapshot
├── docs/
│   └── API_CONTRACT.md                 # Full REST API & ML handoff contract reference
├── tests/
│   ├── test_adapters.py                # Unit tests for signal ingestion adapters
│   ├── test_live_integration.py        # Integration tests for live evaluations
│   ├── test_ml.py                      # Unit tests for ML models, percentiles & memos
│   ├── test_repository.py              # Unit tests for UUIDs and database operations
│   └── test_wave2.py                   # Tests for Kill-Gates, Scorer, Cost Tracker, API
├── .dockerignore                       # Excludes heavy scraper caches from image
├── .env.example                        # Example environment configuration
├── .gitignore                          # Strict gitignore protecting keys & raw caches
├── Dockerfile                          # Production container specification
├── pyproject.toml                      # Pytest & packaging configuration
└── README.md                           # Comprehensive documentation
```

---

## 🏆 MAIN Summit 2026 Hackathon Alignment

- **Sourcing Edge:** Discovers verified European DeepTech ventures at the university spin-off stage before institutional rounds.
- **Rigor:** 5 zero-cost deterministic Kill-Gates eliminate noise, consultancies, and growth-stage companies.
- **Explainability:** 1-page Investment Memos provide clear catalysts, HTGF LP industrial synergies, and devil's advocate risk mitigation.
- **Production-Ready:** Fully typed Pydantic models, persistent SQLite persistence, and sub-second cached queries.
