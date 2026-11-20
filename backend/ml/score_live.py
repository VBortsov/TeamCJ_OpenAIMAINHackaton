"""Score fresh leads (CLI).
Usage: python -m backend.ml.score_live [data/live_candidates_for_ml.csv] [data/ml_predictions.csv]
Per lead: if the optional `accelerator` column is filled -> model_acc, else -> model_noacc.
Missing contract columns are treated as 0 / unknown; fd_* NaN -> 0.
Output columns: company_id, ml_score (0-100, percentile vs. the training reference of the model used), ml_probability, model_used
"""
import sys
from pathlib import Path

import pandas as pd

from .scoring import score_frame

inp = sys.argv[1] if len(sys.argv) > 1 else "data/live_candidates_for_ml.csv"
out = sys.argv[2] if len(sys.argv) > 2 else str(Path(inp).with_name("ml_predictions.csv"))
res = score_frame(pd.read_csv(inp), with_contributions=False).drop(columns="contributions")
res.sort_values("ml_score", ascending=False).to_csv(out, index=False)
print(f"scored {len(res)} leads ({int((res.model_used == 'model_acc').sum())} with accelerator) -> {out}")
print(res.sort_values("ml_score", ascending=False).head(10).to_string(index=False))
