# ML ranking model

What it is: a small logistic regression that ranks fresh leads by how much they resemble early-stage European startups that later raised a round or exited. It outputs `ml_score` (0–100) per `company_id`, which the backend blends 50/50 with the deterministic score.

Code and artefacts live in `backend/ml/`. Feature definitions are in [features.md](features.md).

## How to run

```bash
pip install -r backend/requirements.txt
cp .env.example .env            # put OPENAI_API_KEY=sk-... in .env (git-ignored, never commit it)

# 1. Pipeline exports leads -> data/live_candidates_for_ml.csv (backend/run_wave2.py)
# 2. Score them -> data/ml_predictions.csv
python -m backend.ml.score_live
# or through the API (also writes memos for the top 10):
uvicorn backend.api.main:app --reload
curl -X POST localhost:8000/api/ml/score -H 'Content-Type: application/json' -d '{"memo_top_k": 10}'
```

| Endpoint | What it does |
|---|---|
| `POST /api/ml/score` | Scores `data/live_candidates_for_ml.csv` (or `leads` rows sent in the body), writes `data/ml_predictions.csv`, returns scores with their top drivers, and generates memos for the top `memo_top_k` leads by blended score (default 10, 0 = none) |
| `GET /api/leads/{entity_id}/ml-memo` | Memo for one lead, generated live and cached (`?refresh=true` to regenerate) |
| `GET /api/leads/{entity_id}` | Existing dossier, now with `ml_memo` (null until generated) |

All existing endpoints pick up `ml_score` and `blended_score` automatically once `data/ml_predictions.csv` exists.

Retrain: `python -m backend.ml.train_final` (rewrites `model.joblib` and `metrics.json`), then `python -m backend.ml.build_base_rates`.

## Training data

`backend/ml/train_new_cohort.csv`: 688 European startups (234 German) from accelerator portfolios (YC, Techstars, Antler, EF, SOSV, 500, Alchemist), cohorts 2014–2024. Outcomes were researched on the web in November 2026.

Target: `label_progressed` = raised a round after start, or exit. 368 of 688 are positive (53.5%).

## Model

- Logistic regression (`C=0.3`, balanced classes). It beat gradient boosting and larger feature sets in the search, and it is the most stable on 688 rows.
- Only the two numeric inputs (founder count, cohort year) are standardised. The 0/1 flags are left raw, so the regularisation shrinks rare flags. An earlier version standardised everything, which gave flags seen in under 10 rows extreme weights (a consulting background was worth +2.8 log-odds, a prior exit −4.3). `fd_any_prior_exit` (2 positive rows) is not used at all.
- Two models in one bundle (`model.joblib`):
  - `model_noacc`: default, for leads found before any accelerator. 23 features.
  - `model_acc`: used when the lead's `accelerator` column is filled. Same features plus accelerator dummies.
- `ml_score` = percentile of the lead's predicted probability within the out-of-fold predictions on the training set. A score of 85 means "scores higher than 85% of the reference cohort". It is **not** a probability of success.
- `cohort_year` is a training control only (older cohorts had more time to raise). At scoring it is fixed to 2021 for every lead, so it does not affect the order.
- Missing `fd_*` founder flags are treated as 0. There is no "missing" indicator, so the model cannot reward a lead simply for having been enriched. A founder count of 0 is treated as unknown.
- Descriptions are only used for keyword flags. TF-IDF on description text did not improve cross-validation.

## Quality (honest numbers)

Repeated 5-fold cross-validation (5 repeats). AUC is computed within cohort-year bands so that "older company" does not count as skill.

| | Raised a round or exit (training target) | Series A+ or exit |
|---|---|---|
| Without accelerator (`model_noacc`) | AUC **0.64**. Top 20%: 67% positive vs 54% base. Bottom 30%: 40% | AUC 0.59. Top 20%: 26% vs 20% base |
| With accelerator (`model_acc`) | AUC 0.65. Top 20%: 67% vs 54% base. Bottom 30%: 38% | AUC 0.64. Top 20%: 32% vs 20% base |

How to read this: the model is a weak but real signal. It is better at pushing unlikely leads down (bottom 30% succeed at about 40%) than at picking winners, which is why it is blended with the deterministic score and not used alone.

## Limits

- **Selection:** every training company was in an accelerator, and live leads are found before that. The ranking should transfer, absolute rates will not.
- **Sparse founder data:** `fd_*` flags are filled for 1–28% of training rows, so their weights are noisy and should not be read as findings about founders.
- **Visibility bias:** founders are easier to find for companies that did well. Encoding missing as 0 limits this but does not remove it. Enrich all leads to the same depth before scoring.
- **Small sample:** 688 rows. Differences of a few `ml_score` points between two leads mean nothing.
- **Outcome noise:** outcomes come from web research, and a company with no public trace counts as "did not progress".

## Explanations and memos

`backend/ml/scoring.py` returns per-feature contributions for every lead (weight × (value − training mean), in log-odds, relative to the average training startup). These are shown as `ml_drivers`.

`backend/ml/memo_agent.py` makes one OpenAI call per lead (model from `OPENAI_MEMO_MODEL` in `.env`, default `gpt-6.2-sol`, structured JSON output). Input is only what the pipeline already collected: the feed lead (description, signals with URLs, founders, LP matches, deterministic drivers), scores and rank, `ml_drivers`, and base rates from the training table (`base_rates.json`). Output:

```json
{
  "one_liner": "...",
  "top_signals": [{"signal": "...", "source_url": "https://..."}],
  "team": "...",
  "why_early": "...",
  "devils_advocate_risks": ["..."],
  "next_step": "...",
  "memo_source": "openai:gpt-6.2-sol",
  "ml_drivers": [{"feature": "fd_any_phd", "label": "founder with a PhD", "value": 1.0, "contribution": 0.52}]
}
```

Tested once against the real OpenAI API on 20 November 2026 (3 test leads, all memos returned from `gpt-6.2-sol` in about 11 seconds, roughly 1,700 input and 400 output tokens per memo).

Rules in the prompt: every claim rests on a supplied fact, URLs are only copied from the input and only attached to the fact they belong to, an empty field means unknown and not "no" (empty fields are removed before the call), and `ml_score` is never called a probability. Memos are cached in `data/memo_cache/` and regenerated only if the facts change. The agent respects the shared budget cap in `CostTracker`. If OpenAI is unreachable or the key is missing, a template memo built from the same facts is returned with `memo_source: "fallback_template"`.
