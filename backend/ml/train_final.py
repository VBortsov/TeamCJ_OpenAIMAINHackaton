"""Train the two contract-compliant models on train_new_cohort.csv.
  model_noacc : leads found BEFORE any accelerator (default)      -> features SMALL
  model_acc   : leads already in a programme (accelerator filled) -> SMALL + accelerator dummies
Target: label_progressed (raised a round after start, or exit). Logistic regression won the search (simplest, most stable).
Numeric columns are standardised, 0/1 flags are not (keeps rare founder flags from getting extreme weights).
cohort_year is only a training control; at scoring it is fixed to REF_YEAR for every lead, so it does not change their order.
Usage: python -m backend.ml.train_final  -> model.joblib (both models + reference score distributions), metrics.json
"""
import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent

from .features import ACCELERATORS, REF_YEAR, SMALL, build_features  # noqa: E402

df = pd.read_csv(HERE / "train_new_cohort.csv")
X = build_features(df, cohort_year=df.cohort_year.values)
yb = pd.cut(df.cohort_year, [2013, 2018, 2021, 2022, 2024], labels=False).values
COLS = {"model_noacc": SMALL, "model_acc": SMALL + [f"acc_{a}" for a in ACCELERATORS] + ["acc_other_program"]}


CONT = ["ctrl_cohort_year", "n_founders"]


def make():
    """Only the two numeric columns are standardised. 0/1 flags stay raw, so the L2 penalty shrinks rare flags
    (a flag seen in 9 rows must not move the score by 3 log-odds, which is what standardising all columns did)."""
    scale = ColumnTransformer([("num", StandardScaler(), CONT)], remainder="passthrough", verbose_feature_names_out=False)
    return make_pipeline(scale, LogisticRegression(C=0.3, max_iter=3000, class_weight="balanced"))


def unit_weights(mdl, cols):
    """Effect on the log-odds of +1 in each feature (+1 founder, +1 year, flag 0 -> 1)."""
    z = pd.DataFrame(np.zeros((1, len(cols))), columns=cols)
    base = mdl.decision_function(z)[0]
    w = {}
    for c in cols:
        z1 = z.copy(); z1[c] = 1.0
        w[c] = float(mdl.decision_function(z1)[0] - base)
    return w


def within_auc(y, p, b):
    n = s = 0.0
    for k in np.unique(b):
        m = b == k
        if 0 < y[m].sum() < m.sum():
            s += roc_auc_score(y[m], p[m]) * m.sum(); n += m.sum()
    return s / n


def top_rate(y, p, b, q, top=True):
    r = []
    for k in np.unique(b):
        m = b == k
        if m.sum() >= 10:
            o = np.argsort(-p[m] if top else p[m])[:max(1, int(m.sum() * q))]
            r.append(y[m][o].mean())
    return float(np.mean(r))


metrics, bundle = {}, {"ref_year": REF_YEAR}
for lab in ["label_progressed", "label_series_a_plus_or_exit"]:
    y = df[lab].astype(int).values
    for name, cols in COLS.items():
        w, t20, b30 = [], [], []
        for tr, va in RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=1).split(X, y):
            p = make().fit(X.iloc[tr][cols], y[tr]).predict_proba(X.iloc[va][cols])[:, 1]
            w.append(within_auc(y[va], p, yb[va])); t20.append(top_rate(y[va], p, yb[va], .2)); b30.append(top_rate(y[va], p, yb[va], .3, top=False))
        metrics[f"{name} | target={lab}"] = {"within_year_auc": round(np.mean(w), 3), "top20_rate": round(np.mean(t20), 3),
                                             "bottom30_rate": round(np.mean(b30), 3), "base_rate": round(float(y.mean()), 3)}

y = df.label_progressed.astype(int).values
for name, cols in COLS.items():
    mdl = make().fit(X[cols], y)
    oof = np.zeros(len(y))
    for tr, va in StratifiedKFold(5, shuffle=True, random_state=0).split(X, y):
        Xv = X.iloc[va][cols].copy(); Xv["ctrl_cohort_year"] = REF_YEAR
        oof[va] = make().fit(X.iloc[tr][cols], y[tr]).predict_proba(Xv)[:, 1]
    w = unit_weights(mdl, cols)
    bundle[name] = {"model": mdl, "columns": cols, "reference": np.sort(oof), "weights": w,
                    "means": X[cols].mean().to_dict()}
    metrics[f"{name} weights (log-odds per +1 of the feature; + raises score)"] = pd.Series(w).sort_values().round(3).to_dict()
joblib.dump(bundle, HERE / "model.joblib")
json.dump(metrics, open(HERE / "metrics.json", "w"), indent=2, ensure_ascii=False)
print(json.dumps(metrics, indent=2, ensure_ascii=False))
