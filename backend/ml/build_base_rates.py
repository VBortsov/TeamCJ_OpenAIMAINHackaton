"""Base rates from the training cohort, quoted by the memo agent ("teams with X progressed in A% vs B%").
Usage: python -m backend.ml.build_base_rates -> backend/ml/base_rates.json
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent

from .features import FD, build_features  # noqa: E402

df = pd.read_csv(HERE / "train_new_cohort.csv")
X = build_features(df, cohort_year=df.cohort_year.values)
y = df.label_progressed.astype(int)
FLAGS = ["is_germany", "is_university_city", "solo_founder", "has_cto_founder", "is_deeptech", "mentions_ai",
         "tag_fintech", "tag_b2b", "tag_saas", "country_FRA"] + FD
out = {"label": "label_progressed (raised a round after start, or exit)", "n": int(len(y)),
       "overall_rate": round(float(y.mean()), 3), "features": {}}
for f in FLAGS:
    m = X[f] == 1
    if 10 <= m.sum() <= len(y) - 10:
        out["features"][f] = {"rate_if_1": round(float(y[m].mean()), 3), "n_if_1": int(m.sum()),
                              "rate_if_0": round(float(y[~m].mean()), 3), "n_if_0": int((~m).sum())}
json.dump(out, open(HERE / "base_rates.json", "w"), indent=2)
print(json.dumps(out, indent=2))
