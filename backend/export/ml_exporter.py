"""
ML Inference Contract Exporter.
Exports surviving candidate entities to data/live_candidates_for_ml.csv matching
the schema of our ML teammate's train_new_cohort.csv.
"""

from __future__ import annotations
import csv
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import settings

logger = logging.getLogger(__name__)

ISO2_TO_ISO3 = {
    "DE": "DEU", "CH": "CHE", "AT": "AUT", "NL": "NLD", "FR": "FRA",
    "GB": "GBR", "DK": "DNK", "SE": "SWE", "BE": "BEL", "IT": "ITA",
    "ES": "ESP", "US": "USA", "CA": "CAN", "FI": "FIN", "GR": "GRC",
    "JP": "JPN", "IE": "IRL", "NO": "NOR", "PL": "POL", "PT": "PRT"
}

UNIVERSITY_CITIES = {
    "aachen", "münchen", "munich", "zürich", "zurich", "berlin",
    "darmstadt", "dresden", "karlsruhe", "heidelberg", "tübingen",
    "delft", "lausanne", "oxford", "cambridge", "leoben", "muenster"
}


def export_ml_contract(
    candidates_with_scores: List[Dict[str, Any]],
    output_path: Optional[Path | str] = None
) -> Path:
    """
    Exports candidates to CSV matching the ML inference feature contract.
    """
    target_file = Path(output_path or settings.DATA_DIR / "live_candidates_for_ml.csv")
    target_file.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "company_id", "name", "homepage_url", "country_code", "is_germany", "city",
        "cohort_year", "n_founders_listed", "has_cto_founder", "is_university_city",
        "profile2026_tags", "profile2026_description", "profile2026_is_deeptech",
        "profile2026_mentions_ai", "founder_names", "founder_linkedins",
        "fd_n_founders_found", "fd_any_phd", "fd_any_top_university",
        "fd_any_elite_business_school", "fd_any_stem_degree", "fd_any_studied_abroad",
        "fd_any_bigtech", "fd_any_top_consulting", "fd_any_academic_researcher",
        "fd_any_serial_founder", "fd_any_prior_exit", "fd_team_has_technical_founder",
        "fd_university_spinoff", "is_pre_crunchbase", "htgf_vertical",
        "deterministic_score", "lp_synergy_matches", "inflection_why_now"
    ]

    rows = []
    for cand in candidates_with_scores:
        c2 = (cand.get("country_code") or "DE").upper()
        iso3 = ISO2_TO_ISO3.get(c2, "DEU" if c2 == "DE" else "CHE" if c2 == "CH" else "EU")
        is_de = 1 if iso3 == "DEU" else 0

        # City & University check
        city = cand.get("city") or cand.get("impressum_city") or ""
        is_uni_city = 1 if any(u in city.lower() for u in UNIVERSITY_CITIES) else 0

        # Cohort year
        cohort = 2026
        inc = cand.get("incorporation_date") or ""
        if "2025" in inc or "2025" in str(cand.get("signals", [])):
            cohort = 2025

        tags = cand.get("raw_tags", [])
        desc = cand.get("description", "") or ""
        is_deeptech = 1 if any("deeptech" in t.lower() for t in tags) or "deeptech" in desc.lower() else 0
        mentions_ai = 1 if any("ai" in t.lower() for t in tags) or "ai" in desc.lower() or "neural" in desc.lower() else 0

        founders = cand.get("founders", [])
        f_names = "; ".join(f.get("full_name", "") for f in founders if f.get("full_name"))
        f_linkedins = "; ".join(f.get("linkedin_url", "") for f in founders if f.get("linkedin_url"))

        profile_raw = cand.get("enriched_profile") or {}
        profile = profile_raw.model_dump() if hasattr(profile_raw, "model_dump") else (profile_raw if isinstance(profile_raw, dict) else {})
        breakdown_raw = cand.get("score_breakdown") or {}
        breakdown = breakdown_raw.model_dump() if hasattr(breakdown_raw, "model_dump") else (breakdown_raw if isinstance(breakdown_raw, dict) else {})
        lps = "; ".join(profile.get("lp_synergy_matches", []))

        has_cto = 1 if any(
            "cto" in (f.get("raw_bio", "") or "").lower() for f in founders
        ) or profile.get("fd_team_has_technical_founder", True) else 0

        row = {
            "company_id": cand.get("entity_id"),
            "name": cand.get("canonical_name"),
            "homepage_url": cand.get("website_url") or "",
            "country_code": iso3,
            "is_germany": is_de,
            "city": city,
            "cohort_year": cohort,
            "n_founders_listed": len(founders),
            "has_cto_founder": has_cto,
            "is_university_city": is_uni_city,
            "profile2026_tags": "|".join(tags),
            "profile2026_description": desc,
            "profile2026_is_deeptech": is_deeptech,
            "profile2026_mentions_ai": mentions_ai,
            "founder_names": f_names,
            "founder_linkedins": f_linkedins,
            "fd_n_founders_found": len(founders),
            "fd_any_phd": 1 if profile.get("fd_any_phd") else 0,
            "fd_any_top_university": 1 if profile.get("fd_any_top_university") else 0,
            "fd_any_elite_business_school": 1 if profile.get("fd_any_elite_business_school") else 0,
            "fd_any_stem_degree": 1 if profile.get("fd_any_stem_degree") else 0,
            "fd_any_studied_abroad": 1 if profile.get("fd_any_studied_abroad") else 0,
            "fd_any_bigtech": 1 if profile.get("fd_any_bigtech") else 0,
            "fd_any_top_consulting": 1 if profile.get("fd_any_top_consulting") else 0,
            "fd_any_academic_researcher": 1 if profile.get("fd_any_academic_researcher") else 0,
            "fd_any_serial_founder": 1 if profile.get("fd_any_serial_founder") else 0,
            "fd_any_prior_exit": 1 if profile.get("fd_any_prior_exit") else 0,
            "fd_team_has_technical_founder": 1 if profile.get("fd_team_has_technical_founder") else 0,
            "fd_university_spinoff": 1 if profile.get("fd_university_spinoff") else 0,
            "is_pre_crunchbase": 1 if profile.get("is_pre_crunchbase", True) else 0,
            "htgf_vertical": profile.get("htgf_vertical", "industrial_climate_deeptech"),
            "deterministic_score": cand.get("deterministic_score", breakdown.get("final_score", 0.0)),
            "lp_synergy_matches": lps,
            "inflection_why_now": profile.get("inflection_why_now", "")
        }
        rows.append(row)

    with open(target_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Successfully exported %d candidate records to %s", len(rows), target_file)
    return target_file
