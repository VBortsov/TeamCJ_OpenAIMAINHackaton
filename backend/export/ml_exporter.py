"""
ML Inference Contract Exporter.
Exports surviving candidate entities to data/live_candidates_for_ml.csv matching
the schema of our ML teammate's train_new_cohort.csv and features.md.
"""

from __future__ import annotations
import csv
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.db.repository import normalize_city, clean_url, is_valid_founder_linkedin

logger = logging.getLogger(__name__)

ISO2_TO_ISO3 = {
    "DE": "DEU", "CH": "CHE", "AT": "AUT", "NL": "NLD", "FR": "FRA",
    "GB": "GBR", "DK": "DNK", "SE": "SWE", "BE": "BEL", "IT": "ITA",
    "ES": "ESP", "US": "USA", "CA": "CAN", "FI": "FIN", "GR": "GRC",
    "JP": "JPN", "IE": "IRL", "NO": "NOR", "PL": "POL", "PT": "PRT"
}

UNIVERSITY_CITIES = {
    "aachen", "munich", "zurich", "berlin", "darmstadt", "dresden",
    "karlsruhe", "heidelberg", "tubingen", "delft", "lausanne",
    "oxford", "cambridge", "leoben", "muenster", "bonn", "erlangen",
    "stuttgart", "frankfurt", "hamburg", "cologne", "dusseldorf",
    "freiburg", "wuerzburg", "hannover", "innsbruck", "vienna",
    "basel", "jena", "potsdam", "braunschweig", "london", "paris",
    "amsterdam", "copenhagen", "stockholm"
}

ALLOWED_ACCELERATORS = {"yc", "techstars", "antler", "ef", "sosv", "500", "alchemist", "other"}


DEEPTECH_REGEX = re.compile(
    r"\b(deep[- ]?tech|robotics|quantum|photonics|biotech|semiconductor|hardware|materials|nanobody|medtech|fusion|autonomous|sensor)\b",
    re.IGNORECASE
)
AI_MENTIONS_REGEX = re.compile(
    r"(?i)\b(ai|artificial intelligence|machine learning|llm|neural|deep learning|foundation model|generative|computer vision|nlp)\b"
)


def export_ml_contract(
    candidates_with_scores: List[Dict[str, Any]],
    output_path: Optional[Path | str] = None
) -> Path:
    """
    Exports candidates to CSV matching the ML inference feature contract in features.md.
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
        "fd_university_spinoff", "accelerator", "fd_enriched", "is_pre_crunchbase",
        "htgf_vertical", "deterministic_score", "lp_synergy_matches", "inflection_why_now"
    ]

    rows = []
    for cand in candidates_with_scores:
        raw_c2 = (cand.get("country_code") or "").strip().upper()
        if raw_c2:
            iso3 = ISO2_TO_ISO3.get(raw_c2, "DEU" if raw_c2 == "DE" else "CHE" if raw_c2 == "CH" else "EU")
            is_de = 1 if iso3 == "DEU" else 0
        else:
            iso3 = ""
            is_de = 0

        profile_raw = cand.get("enriched_profile") or {}
        profile = profile_raw.model_dump() if hasattr(profile_raw, "model_dump") else (profile_raw if isinstance(profile_raw, dict) else {})
        breakdown_raw = cand.get("score_breakdown") or {}
        breakdown = breakdown_raw.model_dump() if hasattr(breakdown_raw, "model_dump") else (breakdown_raw if isinstance(breakdown_raw, dict) else {})

        # English normalized city (prioritize clean profile.city over noisy Impressum)
        raw_city = profile.get("city") or cand.get("city") or cand.get("impressum_city") or ""
        city = normalize_city(raw_city) or ""
        tags = cand.get("raw_tags", [])
        desc = cand.get("description", "") or ""
        is_uni_city = 1 if (city and any(u in city.lower() for u in UNIVERSITY_CITIES)) or any(u in desc.lower() for u in UNIVERSITY_CITIES) else 0

        htgf_vert = profile.get("htgf_vertical") or cand.get("htgf_vertical") or ""
        full_text = f"{' '.join(tags)} {desc}"
        is_deeptech = 1 if (htgf_vert in ("industrial_climate_deeptech", "life_sciences_chemistry") or bool(DEEPTECH_REGEX.search(full_text))) else 0
        mentions_ai = 1 if bool(AI_MENTIONS_REGEX.search(full_text)) else 0

        # Accelerator column (exact lowercase values per features.md)
        raw_acc = (cand.get("accelerator") or "").strip().lower()
        accelerator = raw_acc if raw_acc in ALLOWED_ACCELERATORS else ("other" if raw_acc else "")

        # Founders & LinkedIns (real verified profile.founders_enriched take priority over DB)
        raw_founders = [f for f in cand.get("founders", []) if not (f.get("full_name") or "").startswith("Lead PI (")]
        enriched_founders = profile.get("founders_enriched", [])

        if enriched_founders:
            all_f_names = [ef.get("full_name", "").strip() for ef in enriched_founders if ef.get("full_name")]
            all_f_linkedins = [clean_url(ef.get("linkedin_url", "")) for ef in enriched_founders if is_valid_founder_linkedin(ef.get("linkedin_url"))]
        else:
            all_f_names = [f.get("full_name", "").strip() for f in raw_founders if f.get("full_name")]
            all_f_linkedins = [clean_url(f.get("linkedin_url", "")) for f in raw_founders if is_valid_founder_linkedin(f.get("linkedin_url"))]

        all_f_linkedins = [u for u in all_f_linkedins if u]
        f_names = "; ".join(all_f_names)
        f_linkedins = "; ".join(all_f_linkedins)
        n_founders = max(len(raw_founders), len(enriched_founders))

        # Format LP matches cleanly
        raw_lps = profile.get("lp_synergy_matches", [])
        formatted_lps = []
        for item in raw_lps:
            if isinstance(item, dict):
                lp_name = item.get("lp_name", "")
                reason = item.get("reason", "")
                formatted_lps.append(f"{lp_name} ({reason})" if reason else lp_name)
            elif hasattr(item, "lp_name"):
                formatted_lps.append(f"{item.lp_name} ({getattr(item, 'reason', '')})")
            else:
                formatted_lps.append(str(item))
        lps = "; ".join([lp for lp in formatted_lps if lp])

        has_cto = 1 if any(
            re.search(r"(?<![a-z])(?:cto|chief technolog\w*)(?![a-z])", (f.get("raw_bio", "") or "").lower()) for f in raw_founders
        ) or any(
            re.search(r"(?<![a-z])(?:cto|chief technolog\w*)(?![a-z])", (ef.get("role", "") or "").lower()) for ef in enriched_founders
        ) else 0

        # Cohort year
        cohort = profile.get("founding_year") or 2026
        if not profile.get("founding_year"):
            inc = cand.get("incorporation_date") or ""
            if "2025" in inc or "2025" in str(cand.get("signals", [])):
                cohort = 2025

        row = {
            "company_id": cand.get("entity_id"),
            "name": cand.get("canonical_name"),
            "homepage_url": clean_url(cand.get("website_url")) or "",
            "country_code": iso3,
            "is_germany": is_de,
            "city": city,
            "cohort_year": cohort,
            "n_founders_listed": n_founders,
            "has_cto_founder": has_cto,
            "is_university_city": is_uni_city,
            "profile2026_tags": "|".join(tags),
            "profile2026_description": desc,
            "profile2026_is_deeptech": is_deeptech,
            "profile2026_mentions_ai": mentions_ai,
            "founder_names": f_names,
            "founder_linkedins": f_linkedins,
            "fd_n_founders_found": n_founders,
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
            "accelerator": accelerator,
            "fd_enriched": int(profile.get("fd_enriched", 0)),
            "is_pre_crunchbase": 1 if profile.get("is_pre_crunchbase", True) else 0,
            "htgf_vertical": profile.get("htgf_vertical", ""),
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
