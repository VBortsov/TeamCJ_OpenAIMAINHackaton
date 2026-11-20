"""
Glue between the pipeline files in data/ and the ML model.
  data/live_candidates_for_ml.csv  (written by backend.export.ml_exporter)  -> model -> data/ml_predictions.csv
The API reads ml_predictions.csv and blends ml_score with the deterministic score.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from backend.config import settings
from .scoring import load_base_rates, score_frame

logger = logging.getLogger(__name__)

ML_INPUT_PATH = settings.DATA_DIR / "live_candidates_for_ml.csv"
ML_PRED_PATH = settings.DATA_DIR / "ml_predictions.csv"


def run_scoring(leads: Optional[List[Dict[str, Any]]] = None, input_path: Optional[Path] = None,
                write: bool = True) -> pd.DataFrame:
    """Score leads passed in the request, or else the exported CSV. Writes data/ml_predictions.csv."""
    if leads:
        df = pd.DataFrame(leads)
    else:
        path = Path(input_path or ML_INPUT_PATH)
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run the wave-2 pipeline first or POST leads in the body.")
        df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=["company_id", "ml_score", "ml_probability", "model_used", "contributions"])
    res = score_frame(df).sort_values("ml_score", ascending=False).reset_index(drop=True)
    if write:
        ML_PRED_PATH.parent.mkdir(parents=True, exist_ok=True)
        res.drop(columns="contributions").to_csv(ML_PRED_PATH, index=False)
        logger.info("Wrote %d ML scores to %s", len(res), ML_PRED_PATH)
    return res


def explain_lead(entity_id: str) -> Optional[Dict[str, Any]]:
    """ML score and per-feature drivers for one lead, computed from its row in the exported CSV."""
    if not ML_INPUT_PATH.exists():
        return None
    df = pd.read_csv(ML_INPUT_PATH)
    row = df[df["company_id"].astype(str) == str(entity_id)]
    if row.empty:
        return None
    return score_frame(row.head(1)).iloc[0].to_dict()


def base_rates() -> Dict[str, Any]:
    return load_base_rates()


def ensure_predictions() -> None:
    """Keep data/ml_predictions.csv in step with the exported leads: score them when the predictions file is
    missing or older than the export. Scoring is local and free (no OpenAI call). Never raises."""
    try:
        if not ML_INPUT_PATH.exists():
            return
        if ML_PRED_PATH.exists() and ML_PRED_PATH.stat().st_mtime >= ML_INPUT_PATH.stat().st_mtime:
            return
        run_scoring()
    except Exception as e:
        logger.warning("Automatic ML scoring skipped: %s", e)
