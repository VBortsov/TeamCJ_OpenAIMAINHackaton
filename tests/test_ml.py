"""ML ranking model + memo agent (offline: no OpenAI calls)."""
import asyncio

import pandas as pd

from backend.ml.memo_agent import build_memo_input, fallback_memo, generate_memo
from backend.ml.scoring import load_base_rates, score_frame


def _leads():
    return pd.DataFrame([
        {"company_id": "a", "country_code": "DEU", "is_germany": 1, "city": "Aachen", "n_founders_listed": 2,
         "profile2026_tags": "DeepTech|SaaS", "profile2026_is_deeptech": 1, "fd_any_phd": 1,
         "fd_team_has_technical_founder": 1, "fd_university_spinoff": 1, "accelerator": ""},
        {"company_id": "b", "country_code": "DEU", "is_germany": 1, "city": "Berlin", "n_founders_listed": 0},
        {"company_id": "c", "country_code": "DEU", "is_germany": 1, "city": "Berlin"},
        {"company_id": "d", "country_code": "DEU", "is_germany": 1, "city": "Berlin", "accelerator": "yc"},
    ])


def test_scores_are_percentiles_and_ordered():
    res = score_frame(_leads()).set_index("company_id")
    assert res.ml_score.between(0, 100).all()
    assert res.loc["a", "ml_score"] > res.loc["b", "ml_score"]
    assert res.loc["a", "model_used"] == "model_noacc" and res.loc["d", "model_used"] == "model_acc"
    assert any(c["feature"] == "fd_any_phd" and c["contribution"] > 0 for c in res.loc["a", "contributions"])


def test_zero_founders_means_unknown():
    res = score_frame(_leads()).set_index("company_id")
    assert res.loc["b", "ml_probability"] == res.loc["c", "ml_probability"]


def test_memo_input_drops_empty_and_fallback_works(monkeypatch, tmp_path):
    lead = {"entity_id": "a", "name": "aiXopt", "website_url": None, "register_number": "", "founding_year": None,
            "description": "CAD optimisation", "score_breakdown": {"final_score": 75.0, "drivers": ["RWTH spin-off"]},
            "discovered_from": [{"source_name": "eth_rwth", "source_url": "https://rwth-innovation.de", "summary": "Award"}],
            "founders": [{"full_name": "Dr. A", "linkedin_url": None}], "ml_score": 90.0, "blended_score": 82.5}
    ml_row = score_frame(_leads().head(1)).iloc[0].to_dict()
    payload = build_memo_input(lead, 1, 4, ml_row, load_base_rates())
    assert "website_url" not in payload["company"] and "register_number" not in payload["company"]
    assert payload["scores"]["rank_today"] == 1 and payload["ml_drivers"]
    memo = fallback_memo(payload)
    assert memo["top_signals"][0]["source_url"] == "https://rwth-innovation.de"

    from backend.ml import memo_agent
    monkeypatch.setattr(memo_agent.settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(memo_agent, "MEMO_CACHE_DIR", tmp_path)
    out = asyncio.run(generate_memo("a", payload))
    assert out["memo_source"] == "fallback_template" and not list(tmp_path.iterdir())
