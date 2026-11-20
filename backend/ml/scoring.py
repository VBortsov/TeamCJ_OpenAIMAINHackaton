"""Importable scoring: the same logic as score_live.py, plus per-feature contributions for explanations.
Contribution of a feature = weight per unit * (value - training mean), in log-odds relative to the
average training startup. ctrl_cohort_year is constant at scoring, so it is left out of explanations.
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

from .features import build_features, has_accelerator  # noqa: E402

LABELS = {
    "n_founders": "number of founders", "solo_founder": "solo founder", "has_cto_founder": "CTO among founders",
    "is_university_city": "based in a university city", "is_germany": "based in Germany", "country_FRA": "based in France",
    "tag_fintech": "fintech", "tag_b2b": "B2B", "tag_saas": "SaaS", "is_deeptech": "deep tech", "mentions_ai": "mentions AI",
    "fd_any_phd": "founder with a PhD", "fd_any_top_university": "founder from a top university",
    "fd_any_stem_degree": "founder with a STEM degree", "fd_any_bigtech": "founder ex-big tech",
    "fd_any_academic_researcher": "founder was an academic researcher", "fd_any_serial_founder": "serial founder",
    "fd_any_prior_exit": "founder with a prior exit", "fd_any_top_consulting": "founder ex-top consulting",
    "fd_any_elite_business_school": "founder from an elite business school", "fd_any_studied_abroad": "founder studied abroad",
    "fd_team_has_technical_founder": "technical founder on the team", "fd_university_spinoff": "university spin-off",
}


@lru_cache(maxsize=1)
def load_bundle():
    try:
        b = joblib.load(HERE / "model.joblib")
        b["model_noacc"]["weights"]  # older bundle format -> retrain
        return b
    except Exception:  # pickle from another scikit-learn version: retrain from the bundled training table (seconds)
        import subprocess
        subprocess.run([sys.executable, "-m", "backend.ml.train_final"], cwd=HERE.parent.parent, check=True, capture_output=True)
        return joblib.load(HERE / "model.joblib")


@lru_cache(maxsize=1)
def load_base_rates():
    p = HERE / "base_rates.json"
    return json.load(open(p)) if p.exists() else {}


def score_frame(df: pd.DataFrame, with_contributions: bool = True) -> pd.DataFrame:
    """Return one row per lead: ml_score (0-100 percentile), ml_probability, model_used, contributions (list of dicts)."""
    B = load_bundle()
    X = build_features(df)
    used = np.where(has_accelerator(df).values, "model_acc", "model_noacc")
    prob = np.zeros(len(df)); score = np.zeros(len(df)); contribs = [None] * len(df)
    for name in ["model_acc", "model_noacc"]:
        m = used == name
        if not m.any():
            continue
        b = B[name]
        Xm = X.loc[m, b["columns"]]
        prob[m] = b["model"].predict_proba(Xm)[:, 1]
        score[m] = np.searchsorted(b["reference"], prob[m], side="right") / len(b["reference"]) * 100
        if with_contributions:
            # contribution = weight per unit * (value - training mean): log-odds relative to the average training startup
            w = np.array([b["weights"][f] for f in b["columns"]])
            mu = np.array([b["means"][f] for f in b["columns"]])
            c = (Xm.values - mu) * w
            for row_i, idx in enumerate(np.where(m)[0]):
                items = []
                for j, f in enumerate(b["columns"]):
                    if f == "ctrl_cohort_year" or abs(c[row_i, j]) < 0.01:
                        continue
                    items.append({"feature": f, "label": LABELS.get(f, f), "value": float(Xm.values[row_i, j]),
                                  "contribution": round(float(c[row_i, j]), 3)})
                contribs[idx] = sorted(items, key=lambda d: -abs(d["contribution"]))
    ids = df["company_id"] if "company_id" in df else pd.Series(range(len(df)), index=df.index)
    return pd.DataFrame({"company_id": ids.values, "ml_score": np.round(score, 1), "ml_probability": np.round(prob, 4),
                         "model_used": used, "contributions": contribs})
