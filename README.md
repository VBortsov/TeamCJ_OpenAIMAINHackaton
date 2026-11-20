# TeamCJ_OpenAIMAINHackaton

**Early-Stage European VC Startup Sourcing Engine**  
*MAIN Summit 2026 — Track: High-Tech Gründerfonds (HTGF)*  
*Focus: ~70% Germany/DACH, Secondary Europe (Pre-Seed/Seed DeepTech, AI, Industrial, Life Sciences)*

---

## 🚀 Wave 1: Git Setup + Ingestion & Storage Layer

Wave 1 provides an async Python ingestion package with local disk HTTP caching, resilient multi-source scraping, SQLite database persistence, and snapshot exports for rapid downstream integration (UI, scoring algorithms, n8n workflows, ElevenLabs audio briefings).

### 📦 Key Features & Architecture
- **Multi-Source Ingestion Adapters**:
  1. `eth_rwth`: Scrapes official ETH Zurich Spin-offs/Start-ups (2025–2026 cohorts) and RWTH Aachen Spin-off Award winners (aiXopt, Athenyx Robotics, brixl, Determion, H2ALL, Senlytics).
  2. `openalex`: High-efficiency filtered list queries ($0.0001/call) targeting top DACH/EU research labs (ETH Zurich, TUM, RWTH Aachen, Max Planck, Fraunhofer, TU Delft) co-authoring scientific papers with non-Big-Tech commercial startups, with academic founder linkage.
  3. `hf_hub`: Ingests trending open-weights models and AI organizations from Hugging Face Hub (filtered for independent startups), extracting downloads, `arxiv:*` references, and member handles.
  4. `luma_events`: Scrapes public tech and demo-day event platforms across German hubs (Berlin, Munich) to discover host startups and community founders.
- **Disk HTTP Cache (`data/http_cache/`)**:
  - Keyed by SHA256 of request signature to guarantee sub-second debug re-runs and prevent rate-limit burns.
- **SQLite Database (`data/scout.db`)**:
  - Tables engineered for Waves 1–3 without breaking schema migrations: `entities`, `source_signals`, `founders`, `entity_founder_links`, and `funding_blocklist`.
- **JSON Snapshot (`data/wave1_candidates.json`)**:
  - Denormalized export ready for frontend UI, scoring pipeline, and n8n webhooks.

---

## 🛠️ Quickstart Guide

### 1. Setup Virtual Environment
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r backend/requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
```
*(Leave optional keys empty or supply your own: `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `OPENALEX_API_KEY`, `HF_TOKEN`, `GITHUB_TOKEN`).*

### 3. Run Ingestion Pipeline
```bash
python -m backend.ingestion.run_wave1 --limit 30
```

### 4. Run Automated Test Suite
```bash
pytest -v
```
