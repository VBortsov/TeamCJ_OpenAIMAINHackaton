# Startup features

All features we have per startup, which of them the ranking model uses, and which must stay out.

Source table: `backend/ml/train_new_cohort.csv`, 688 European startups from accelerator portfolios (YC, Techstars, Antler, EF, SOSV, 500, Alchemist), cohorts 2014–2024, outcomes researched in November 2026. Live leads from the search agent arrive as `live_candidates_for_ml.csv` with the same column names.

"Fill" is the share of training rows where the column is not empty. "Success rate" is the share with `label_progressed = 1` (raised a round after start, or exit); the overall rate is **53.5%**. These are correlations in the training data, not causal effects, and small groups (n < 30) are noisy.

## 1. Input columns from the scraper (used by the model)

These are guaranteed online for every fresh lead.

### Geography

| Column | Type | Fill | Notes |
|---|---|---|---|
| `country_code` | ISO-3 string (DEU, CHE, AUT, NLD, FRA, DNK, …) | 100% | One-hot for DEU, FRA, NLD, DNK, CHE, AUT |
| `is_germany` | 0/1 | 100% | Falls back to `country_code == DEU` if empty |
| `city` | string | 100% | Mapped to hubs: Berlin, Munich (incl. Garching), Paris, Amsterdam, Copenhagen, Zurich, Hamburg |
| `is_university_city` | 0/1 | 100% | |

### Product / text

| Column | Type | Fill | Notes |
|---|---|---|---|
| `profile2026_description` | text | 100% | Only used for keyword flags (see `tag_*`). TF-IDF/embeddings did not help in CV |
| `profile2026_tags` | `|`-separated | 96% | Keyword flags `tag_*` |
| `profile2026_is_deeptech` | 0/1 | 100% | |
| `profile2026_mentions_ai` | 0/1 | 100% | |

### Team (basic)

| Column | Type | Fill | Notes |
|---|---|---|---|
| `n_founders_listed` | int | 45% | Falls back to `fd_n_founders_found`, then 2; clipped to 1–6. 0 is read as unknown |
| `fd_n_founders_found` | int | 70% | |
| `has_cto_founder` | 0/1 | 45% | Empty → 0 |
| `fd_team_has_technical_founder` | 0/1 | 28% | Empty → 0 |
| `fd_university_spinoff` | 0/1 | 25% | ETH / RWTH / TUM / ERC-PoC etc. Empty → 0 |

### Founder deep features (`fd_*`, enriched via Impressum + OpenAlex + web search)

Empty is encoded as **0**, with no separate "missing" flag, so the model cannot learn "was enriched". Enrich all leads equally, not only the top 25/50, otherwise enriched leads get a bonus just for being enriched.

| Column | Fill | Success if 1 (n) | Success if 0 |
|---|---|---|---|
| `fd_any_phd` | 4% | 75% (28) | 53% |
| `fd_any_top_university` | 4% | 70% (23) | 53% |
| `fd_any_stem_degree` | 12% | 70% (74) | 52% |
| `fd_any_bigtech` (ex-Google/Meta/Nvidia/Bosch/Siemens/SAP/Celonis…) | 4% | 55% (11) | 54% |
| `fd_any_academic_researcher` | 4% | 71% (28) | 53% |
| `fd_any_serial_founder` | 4% | 67% (24) | 53% |
| `fd_any_prior_exit` | 1% | n < 10 | |
| `fd_any_top_consulting` | 2% | n < 10 | |
| `fd_any_elite_business_school` | 1% | n < 10 | |
| `fd_any_studied_abroad` | 3% | 79% (19) | 53% |
| `fd_team_has_technical_founder` | 28% | 68% (191) | 48% |
| `fd_university_spinoff` | 25% | 86% (14) | 53% |

### Optional

| Column | Notes |
|---|---|
| `accelerator` | yc / techstars / antler / ef / sosv / 500 / alchemist / other. If filled, the accelerator-aware model (`model_acc`) is used; if empty (the normal case for pre-accelerator leads), `model_noacc` |

## 2. Features the model builds (`backend/ml/features.py`)

`model_noacc` uses the compact set below; `model_acc` adds the `acc_*` dummies. Coefficients are from the logistic regression on standardised inputs (+ raises the score).

| Feature | Built from | Coef (noacc) | Success if 1 (n) vs 0 |
|---|---|---|---|
| `n_founders` | `n_founders_listed` / `fd_n_founders_found` | +0.36 | |
| `fd_any_top_consulting` | input | +0.32 | |
| `fd_team_has_technical_founder` | input | +0.30 | 68% (191) vs 48% |
| `solo_founder` | n_founders == 1 | +0.30 | 58% (123) vs 52% |
| `is_deeptech` | `profile2026_is_deeptech` | +0.21 | 59% (235) vs 51% |
| `fd_university_spinoff` | input | +0.20 | 86% (14) vs 53% |
| `tag_fintech` | tags + description contain "fintech" | +0.16 | 59% (87) vs 53% |
| `tag_saas` | "saas" | +0.15 | 58% (100) vs 53% |
| `fd_any_elite_business_school` | input | +0.13 | |
| `has_cto_founder` | input | +0.12 | 63% (106) vs 52% |
| `fd_any_phd` | input | +0.11 | 75% (28) vs 53% |
| `fd_any_studied_abroad` | input | +0.07 | |
| `fd_any_serial_founder` | input | +0.06 | |
| `is_university_city` | input | +0.02 | 55% (413) vs 51% |
| `fd_any_top_university` | input | +0.02 | |
| `tag_b2b` | "b2b" | +0.02 | |
| `mentions_ai` | `profile2026_mentions_ai` | +0.01 | 52% (226) vs 54% |
| `fd_any_bigtech` | input | −0.01 | |
| `fd_any_stem_degree` | input | −0.03 | |
| `fd_any_academic_researcher` | input | −0.03 | |
| `country_FRA` | `country_code` | −0.07 | 52% (240) vs 54% |
| `is_germany` | input | −0.11 | 53% (234) vs 54% |
| `fd_any_prior_exit` | input | −0.23 | (only 1% filled, noisy) |
| `ctrl_cohort_year` | training control only | −0.43 | Fixed to 2021 for every live lead, so it does not change their order |

Built but not in the compact set (available for experiments): other `country_*`, `hub_*` (7 cities), more `tag_*` (healthcare, climate, artificial intelligence, hardware, consumer, marketplace, biotech, robotics, developer tools, energy, education, proptech, logistics), `fd_n_positive_signals` (sum of fd flags).

Accelerator dummies in `model_acc`: `acc_yc` +0.19, `acc_500` +0.09, `acc_ef` +0.06, `acc_antler` −0.03, `acc_sosv` −0.11, `acc_techstars` −0.15, `acc_alchemist` −0.02, `acc_other_program` 0.

## 3. Columns that must NOT go into the model

| Columns | Why |
|---|---|
| `outcome_*`, `outcome26_*`, `label_*`, `outcome_grade` | Future outcomes: target leakage |
| `accelerator`, `accelerator_program`, `also_in_other_accelerator` (in `model_noacc`) | We find startups before accelerators; empty at inference |
| `cohort_year` | All live leads are 2025/26, training is 2014–24; used only as a fixed control |
| `fd_mean_age_at_cutoff` | Filled in 2 of 688 rows |
| `fd_any_imo_olympiad` | Always 0 |
| `research_confidence`, `research_sources` | Measure how findable a company is online (visibility leak, AUC 0.75+ on its own) |
| `company_id`, `name`, `homepage_url`, `founder_names`, `founder_linkedins`, `source`, `source_url`, `random_order` | Identifiers / service columns. Kept for the memo and for joins |

## 4. Labels (targets)

| Label | Definition | Positives |
|---|---|---|
| `label_progressed` | Raised a round after start, or exit. **Used to train the model** | 368 / 688 (53.5%) |
| `label_series_a_plus_or_exit` | Series A or later, or exit | 135 / 688 (19.6%) |
| `label_survived_2026` | Active in 2026 (only rows with known status) | 67% filled |
| `label_survived_2026_unknown_dead` | Same, unknown counted as dead | |
| `label_exit`, `label_dead_by_source` | Exit / marked dead by the portfolio source | |

## 5. Signals the search agent adds on top (not in the training table)

Not used by `ml_score`; they go into the team's `deterministic_score` and the blend: Hugging Face, GitHub activity, Impressum data, HTGF LP synergies, website facts. Each should carry a URL and date so it can be cited.

## 6. Check of the live export (`backend/export/ml_exporter.py`)

Column names and types match what the model reads. Differences in how values are computed, most important first:

| Column | In the export | Effect on `ml_score` |
|---|---|---|
| `profile2026_is_deeptech` | 1 only if the literal word "deeptech" is in tags or description | Medium. Misses "deep tech", robotics, biotech, hardware, so real deep-tech leads get a clearly lower score. Better: set from `htgf_vertical` or a wider keyword list |
| `has_cto_founder` | Also set to 1 whenever `fd_team_has_technical_founder` is true | Medium. One fact is counted twice (CTO +0.12 and technical founder +0.30) |
| `n_founders_listed`, `fd_n_founders_found` | 0 when no founders were found | Fixed on the model side: 0 is now read as unknown |
| `fd_*` | 0 for every non-enriched lead | Fine, as long as all leads are enriched to the same depth. Otherwise enriched leads rank higher just for being enriched (`fd_enriched` shows which) |
| `profile2026_mentions_ai` | 1 if the letters "ai" appear anywhere ("maintain", "available") | Negligible (weight +0.01), but the flag is almost always 1 |
| `is_university_city` | Own city list, and also 1 if a city name appears in the description | Negligible (weight +0.02) |
| `country_code`, `is_germany` | Missing country defaults to DE | Small (weight −0.11), but it is an assumed value |
| `accelerator` | Filled for YC leads | Those leads go through `model_acc`. Intended |
| `cohort_year`, `fd_enriched`, `is_pre_crunchbase`, `htgf_vertical`, `deterministic_score`, `lp_synergy_matches`, `inflection_why_now` | Extra columns | Ignored by the model |
