# API & Integration Contract Reference

**HTGF Early-Stage European Sourcing Engine**  
*MAIN Summit 2026 — Track: High-Tech Gründerfonds (HTGF)*  
*Backend Version: 2.5.0*

---

## 1. Machine Learning Integration Contract

The backend automatically exports pre-screened, deeply-enriched leads to `data/live_candidates_for_ml.csv` following the exact 36-column contract defined by the ML modeling team.

### 1.1 ML Input Contract: `data/live_candidates_for_ml.csv`
Every row represents a surviving early-stage DeepTech lead that has passed all 5 deterministic Kill-Gates and post-enrichment age filters.

| Column | Type | Description | Allowed Values / Examples |
| :--- | :---: | :--- | :--- |
| `company_id` | string | UUIDv5 deterministic identifier | `0c6b12a0-47b2-5f6a-939e-e8b2b9f3ac11` |
| `name` | string | Canonical startup name | `aiXopt`, `Athenyx Robotics` |
| `homepage_url` | string | Normalized startup website (clean) | `https://aixopt.ai` |
| `country_code` | string | 3-letter ISO-3166 alpha-3 code | `DEU`, `CHE`, `AUT`, `NLD`, `FRA`, `GBR` |
| `is_germany` | int (0/1) | 1 if Germany (`DEU`), 0 otherwise | `1`, `0` |
| `city` | string | Normalized English hub name | `Aachen`, `Munich`, `Zurich`, `Cologne` |
| `cohort_year` | int | Real founding / incorporation year | `2024`, `2025`, `2026` |
| `n_founders_listed` | int | Total verified co-founders | `1`, `2`, `3` |
| `has_cto_founder` | int (0/1) | 1 if technical founder / CTO verified | `1`, `0` |
| `is_university_city` | int (0/1) | 1 if hub has top technical university | `1`, `0` |
| `profile2026_tags` | string | Semicolon-delimited industry tags | `DeepTech; AI; Industrial Automation` |
| `profile2026_description` | string | Concise verified venture summary | Text |
| `profile2026_is_deeptech` | int (0/1) | 1 if proprietary scientific / hard tech | `1`, `0` |
| `profile2026_mentions_ai` | int (0/1) | 1 if core AI / machine learning | `1`, `0` |
| `founder_names` | string | Semicolon-delimited verified founders | `Dr. David Stenger; Tim Reuscher` |
| `founder_linkedins` | string | Semicolon-delimited personal LinkedIns | `https://linkedin.com/in/...` |
| `fd_n_founders_found` | int | Count of verified founder profiles | `1`, `2`, `3` |
| `fd_any_phd` | int (0/1) | 1 if any founder holds a doctorate | `1`, `0` |
| `fd_any_top_university` | int (0/1) | 1 if ETH, RWTH, TUM, Oxford, etc. | `1`, `0` |
| `fd_any_elite_business_school` | int (0/1) | 1 if INSEAD, WHU, St. Gallen, etc. | `1`, `0` |
| `fd_any_stem_degree` | int (0/1) | 1 if engineering / natural sciences | `1`, `0` |
| `fd_any_studied_abroad` | int (0/1) | 1 if international university record | `1`, `0` |
| `fd_any_bigtech` | int (0/1) | 1 if ex-Google, Meta, Apple, etc. | `1`, `0` |
| `fd_any_top_consulting` | int (0/1) | 1 if ex-McKinsey, BCG, Bain | `1`, `0` |
| `fd_any_academic_researcher` | int (0/1) | 1 if post-doc / lab researcher | `1`, `0` |
| `fd_any_serial_founder` | int (0/1) | 1 if prior founding track record | `1`, `0` |
| `fd_any_prior_exit` | int (0/1) | 1 if successful prior exit | `1`, `0` |
| `fd_team_has_technical_founder`| int (0/1) | 1 if verified technical founder | `1`, `0` |
| `fd_university_spinoff` | int (0/1) | 1 if university spin-off origin | `1`, `0` |
| `accelerator` | string | Accelerator name (`features.md` spec) | `yc`, `techstars`, `antler`, `ef`, `other`, `""` |
| `fd_enriched` | int (0/1) | 1 if enriched via live web search | `1`, `0` |
| `is_pre_crunchbase` | int (0/1) | 1 if undiscovered / pre-institutional | `1`, `0` |
| `htgf_vertical` | string | HTGF Investment Vertical | `industrial_climate_deeptech`, `digital_tech_ai`, `life_sciences_chemistry` |
| `deterministic_score` | float | 0–100 transparent rule-based score | `75.0`, `84.5` |
| `lp_synergy_matches` | string | Semicolon-delimited HTGF IV LPs | `Robert Bosch GmbH (Automation); Evonik` |
| `inflection_why_now` | string | Concise investment thesis why now | Text |

---

### 1.2 ML Output Hook: `data/ml_predictions.csv`
If the ML teammate outputs `data/ml_predictions.csv`, the backend automatically picks it up on the next request and computes a blended score:

```
blended_score = round(0.5 * deterministic_score + 0.5 * ml_score, 1)
```

**Required CSV Format:**
```csv
company_id,ml_score
0c6b12a0-47b2-5f6a-939e-e8b2b9f3ac11,91.4
38a16db2-944a-5f09-9069-b5bf93ec8972,82.0
```

When `data/ml_predictions.csv` is absent, the backend falls back cleanly to `ml_score: null` and `blended_score: final_score` with zero crashes.

---

## 2. FastAPI Endpoints Reference

Base URL (local): `http://localhost:8000`  
Interactive OpenAPI Swagger Docs: `http://localhost:8000/docs`

### 2.1 System & Monitoring

#### `GET /api/health`
Returns system health, database record counts, and API spend / cache tracking metrics.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "2.5.0",
  "timestamp": "2026-11-20T19:35:00.000000+00:00",
  "database": {
    "path": "data/scout.db",
    "total_entities": 148,
    "total_signals": 194
  },
  "cost_and_cache": {
    "cached_leads_count": 124,
    "cumulative_spend_usd": 0.0,
    "max_budget_cap_usd": 2.0
  }
}
```

---

### 2.2 Provenance Dashboard & Noise Audit

#### `GET /api/dashboard`
Returns the complete provenance snapshot: conversion funnel metrics, all surviving ranked leads, and the full rejected noise audit trail.

**Response Structure (200 OK):**
```json
{
  "meta": {
    "generated_at": "2026-11-20T19:35:00+00:00",
    "total_raw": 148,
    "passed_kill_gates": 64,
    "enriched_count": 64,
    "germany_dach_share_pct": 78.1
  },
  "funnel": {
    "raw_ingested": 148,
    "gate1_hobby_failed": 42,
    "gate2_consultancy_failed": 18,
    "gate3_corporate_age_failed": 12,
    "gate4_fake_stars_failed": 4,
    "gate5_already_funded_failed": 8,
    "post_enrichment_age_kills": 2,
    "survived_all_gates": 64
  },
  "ranked_leads": [ /* Array of Lead objects */ ],
  "rejected_audit": [ /* Array of RejectedEntity objects */ ]
}
```

#### `GET /api/rejected`
Returns the specific noise entities filtered out by Gates 1–5 to prove sourcing rigor to partners and judges.

**Response (200 OK):**
```json
{
  "total_rejected": 84,
  "rejected_entities": [
    {
      "entity_id": "9b12...",
      "canonical_name": "Acme Web Solutions",
      "country_code": "DE",
      "website_url": "https://acme-web.de",
      "discovered_from": ["Luma Events"],
      "kill_reasons": ["Gate 2: Software agency / IT consultancy without proprietary scientific IP"]
    }
  ]
}
```

---

### 2.3 Leads & Partner Investment Memos

#### `GET /api/leads`
Query ranked leads with optional filters.

**Query Parameters:**
- `vertical` (string, optional): Filter by `industrial_climate_deeptech`, `digital_tech_ai`, or `life_sciences_chemistry`.
- `country` (string, optional): 2-letter country code (e.g., `DE`, `CH`, `AT`).
- `min_score` (float, optional, default `0.0`): Minimum score threshold (0.0 to 100.0).

**Response (200 OK):**
```json
{
  "total_matching": 10,
  "leads": [
    {
      "entity_id": "0c6b12a0-47b2-5f6a-939e-e8b2b9f3ac11",
      "name": "aiXopt",
      "canonical_name": "aiXopt",
      "website_url": "https://aixopt.ai",
      "country_code": "DE",
      "city": "Aachen",
      "legal_name": "aiXopt GmbH",
      "legal_form": "GmbH",
      "register_number": "HRB 28491",
      "founding_year": 2025,
      "htgf_vertical": "industrial_climate_deeptech",
      "description": "AI-accelerated structural optimization for automotive and aerospace components.",
      "discovered_from": [
        {
          "source_name": "eth_rwth",
          "source_label": "RWTH Aachen Spin-off Award",
          "signal_date": "2025-06-15",
          "source_url": "https://rwth-innovation.de",
          "summary": "Official Spin-off Award winner"
        }
      ],
      "deterministic_score": 75.0,
      "ml_score": null,
      "blended_score": 75.0,
      "score_breakdown": {
        "team_pedigree": 30.0,
        "commercial_intent": 4.0,
        "technical_moat": 16.0,
        "multi_signal_convergence": 15.0,
        "htgf_mandate_fit": 10.0,
        "final_score": 75.0,
        "drivers": [
          "Spin-off from RWTH Aachen with verified doctorate co-founders",
          "HTGF IV LP synergy with Robert Bosch GmbH in industrial design"
        ],
        "evidence_urls": [
          "https://aixopt.ai",
          "https://rwth-innovation.de"
        ]
      },
      "earliness_badge": "Pre-Crunchbase / Undiscovered",
      "inflection_why_now": "Rapid industrial demand for lightweight automotive CAD optimization under EU emission targets.",
      "founders": [
        {
          "full_name": "Dr. David Stenger",
          "role": "Co-Founder & CEO",
          "linkedin_url": "https://de.linkedin.com/in/david-stenger-123",
          "has_phd": true,
          "is_ex_bigtech": false,
          "prior_companies": [],
          "universities": ["RWTH Aachen University"],
          "source_of_identity": "llm_web_search"
        }
      ],
      "lp_synergy_matches": [
        {
          "lp_name": "Robert Bosch GmbH",
          "reason": "Additive manufacturing CAD/CAE co-development"
        }
      ]
    }
  ]
}
```

#### `GET /api/leads/{entity_id}`
Generates an auditable 1-page Investment Memo structure for partner review and IC evaluation.

**Response Structure (200 OK):**
- `entity_id`, `name`, `canonical_name`, `legal_name`, `website_url`, `country_code`, `city`, `founding_year`, `htgf_vertical`, `earliness_badge`
- `overall_score`, `deterministic_score`, `ml_score`, `blended_score`
- `executive_summary`: Concise business & technology overview
- `investment_thesis`: `{"why_now": "...", "catalyst": "..."}`
- `team_dossier`: `{"founders": [...], "has_phd": true, "pedigree_score": 30.0}`
- `technology_moat`: `{"spin_off_origin": [...], "moat_score": 16.0, "ip_status": "..."}`
- `commercial_traction`: `{"register_number": "HRB 28491", "commercial_score": 4.0, "status": "..."}`
- `lp_synergy_analysis`: Array of matched HTGF IV LPs with strategic rationales
- `devils_advocate_risks`: Concrete risks and mitigation vectors
- `evidence_urls`: Clickable primary verification URLs
- `score_drivers`: Highlighted score catalysts

---

### 2.4 Founder Technical Profile Verification

#### `GET /api/founders/github/{username}`
Queries GitHub public API to verify technical founder traction, open-source repositories, total stars, forks, and language proficiencies.

**Path Parameters:**
- `username`: GitHub handle (e.g., `torvalds`, or startup CTO username).

**Response (200 OK):**
```json
{
  "username": "testfounder",
  "profile_url": "https://github.com/testfounder",
  "name": "Dr. Tech Founder",
  "bio": "AI & Robotics researcher at RWTH Aachen",
  "company": "RoboTech Spin-off",
  "location": "Munich, Germany",
  "blog": "https://testfounder.dev",
  "public_repos": 14,
  "followers": 480,
  "total_stars": 1250,
  "total_forks": 180,
  "top_languages": ["Python", "C++", "Rust"],
  "top_repos": [
    {
      "name": "deep-robotics",
      "url": "https://github.com/testfounder/deep-robotics",
      "stars": 950,
      "forks": 130,
      "language": "Python",
      "pushed_at": "2026-02-15T12:00:00Z"
    }
  ],
  "social_accounts": [
    {
      "provider": "twitter",
      "url": "https://x.com/testfounder"
    }
  ]
}
```

**Error Response (404 Not Found):**
```json
{
  "detail": "GitHub user 'unknown_user_123' not found"
}
```

---

### 2.5 Live On-Demand Enrichment

#### `POST /api/enrich/live`
Performs end-to-end evaluation on ANY arbitrary URL or company name on-demand in seconds for live jury Q&A demonstrations.

**Request Body:**
```json
{
  "domain_or_name": "https://aixopt.ai"
}
```

**Response (200 OK):**
```json
{
  "canonical_name": "aiXopt GmbH",
  "domain": "aixopt.ai",
  "city": "Aachen",
  "gate_passed": true,
  "kill_reasons": [],
  "final_score": 75.0,
  "score_breakdown": {
    "team_pedigree": 30.0,
    "commercial_intent": 4.0,
    "technical_moat": 16.0,
    "multi_signal_convergence": 15.0,
    "htgf_mandate_fit": 10.0,
    "final_score": 75.0
  },
  "htgf_vertical": "industrial_climate_deeptech",
  "inflection_why_now": "Rapid industrial demand for lightweight automotive CAD optimization.",
  "founders_enriched": [ /* Verified founder profiles */ ],
  "lp_synergy_matches": [ /* Matched LPs */ ]
}
```

---

### 2.6 Outbound Webhook Integrations

#### `POST /api/dispatch/n8n/{entity_id}`
Dispatches a top lead payload to n8n workflow for Lusha contact enrichment and Slack notifications.

**Response (200 OK — Real or Simulated):**
```json
{
  "status": "simulated_dispatch",
  "note": "N8N_WEBHOOK_URL not configured; returning verified dispatch contract payload for UI/n8n integration testing.",
  "payload": {
    "event": "htgf_top_lead_identified",
    "dispatched_at": "2026-11-20T19:35:00+00:00",
    "lead_id": "0c6b12a0-47b2-5f6a-939e-e8b2b9f3ac11",
    "company_name": "aiXopt",
    "website_url": "https://aixopt.ai",
    "country": "DE",
    "city": "Aachen",
    "score": 75.0,
    "vertical": "industrial_climate_deeptech",
    "earliness_badge": "Pre-Crunchbase / Undiscovered",
    "why_now": "Rapid industrial demand for automotive CAD optimization.",
    "founders_to_enrich": [
      {
        "name": "Dr. David Stenger",
        "role": "Founder",
        "linkedin": "https://de.linkedin.com/in/david-stenger-123"
      }
    ],
    "lp_synergies": ["Robert Bosch GmbH", "Evonik Venture Capital"],
    "key_drivers": ["Spin-off from RWTH Aachen with verified doctorate co-founders"]
  }
}
```


---

## ML Scoring & Memo Endpoints

Full model documentation: [model_docs.md](../model_docs.md). Feature list: [features.md](../features.md).

#### `POST /api/ml/score`
Runs the ML ranking model on `data/live_candidates_for_ml.csv` (or on `leads` rows sent in the body, same schema), writes `data/ml_predictions.csv`, and generates LLM memos for the top-K leads by `blended_score`.

**Body (all optional):** `{"memo_top_k": 10, "refresh_memos": false, "leads": null}`

**Response (200 OK):**
```json
{
  "scored": 42,
  "predictions_path": "data/ml_predictions.csv",
  "scores": [
    {"company_id": "…", "ml_score": 95.8, "ml_probability": 0.8353, "model_used": "model_noacc",
     "ml_drivers": [{"feature": "fd_any_phd", "label": "founder with a PhD", "value": 1.0, "contribution": 0.521}]}
  ],
  "memos": [
    {"rank": 1, "entity_id": "…", "name": "aiXopt", "blended_score": 82.9, "ml_score": 95.8,
     "memo": {
       "one_liner": "…",
       "top_signals": [{"signal": "…", "source_url": "https://…"}],
       "team": "…",
       "why_early": "…",
       "devils_advocate_risks": ["…"],
       "next_step": "…",
       "memo_source": "openai:gpt-4o-mini",
       "ml_drivers": []
     }}
  ]
}
```

#### `GET /api/leads/{entity_id}/ml-memo?refresh=false`
Returns the same `memo` object for one lead (cached on disk, generated on first call).

**UI notes:**
- `ml_score` is a percentile (0–100), not a probability. Label it "ML rank" or similar.
- `top_signals[].source_url` can be `null`. `memo_source` is `"fallback_template"` when OpenAI was unavailable.
- `GET /api/leads/{entity_id}` now includes `ml_memo` (`null` until generated).
