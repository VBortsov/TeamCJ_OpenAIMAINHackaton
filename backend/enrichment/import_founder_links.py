"""
Offline Founder Profiles & Social Links Merger.
Ingests verified founder LinkedIn URLs, GitHub handles, cities, and countries from
data/founder_profiles_enrichment.json (generated for $0 via Gemini Deep Research web UI)
and merges them directly into data/llm_cache/<entity_id>.json and SQLite database.
"""

from __future__ import annotations
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.db.repository import clean_url, is_valid_founder_linkedin, normalize_city, slugify, generate_founder_id
from backend.enrichment.openai_enricher import clean_github_handle

logger = logging.getLogger(__name__)

HONORIFICS_REGEX = re.compile(r"\b(dr|prof|phd|dipl-ing|dipl\.-ing\.|msc|bsc|mba|herr|frau)\b\.?", re.IGNORECASE)

OPERATIONAL_HUBS = {"zurich", "zürich", "munich", "münchen", "aachen"}
TAX_DOMICILES = {
    "cham", "olten", "wohlen", "schaffhausen", "küsnacht", "kuesnacht",
    "herford", "grünwald", "gruenwald"
}


def normalize_tokens(text: str) -> List[str]:
    """Tokenize and strip honorifics/punctuation for fuzzy founder name matching."""
    cleaned = HONORIFICS_REGEX.sub("", (text or "").lower())
    tokens = re.findall(r"[a-z0-9äöüß]+", cleaned)
    return [t for t in tokens if len(t) > 1]


def names_match(name1: str, name2: str) -> bool:
    """Fuzzy token-safe match for founder full names (e.g. 'Dr. Jonas Keller' ~ 'Jonas Keller')."""
    tokens1 = set(normalize_tokens(name1))
    tokens2 = set(normalize_tokens(name2))
    if not tokens1 or not tokens2:
        return False
    # Exact token match or subset match (first + last name match)
    if tokens1 == tokens2:
        return True
    if len(tokens1) >= 2 and len(tokens2) >= 2:
        # Check if first and last tokens overlap
        return len(tokens1 & tokens2) >= 2
    return False


def load_enrichment_payload(path: Path) -> List[Dict[str, Any]]:
    """Loads founder_profiles_enrichment.json regardless of whether it is a list or dict."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # If keyed by company name or id
            items = []
            for k, v in data.items():
                if isinstance(v, dict):
                    if len(k) == 36 and k.count("-") == 4:
                        v.setdefault("entity_id", k)
                    else:
                        v.setdefault("company_name", k)
                    items.append(v)
            return items
    except Exception as e:
        logger.error("Failed to parse founder enrichment file %s: %s", path, e)
    return []


def merge_offline_founder_profiles(
    enrichment_file: Path | str,
    cache_dir: Path | str,
    db_path: Path | str
) -> Dict[str, int]:
    """
    Merges offline founder intelligence into local disk cache and SQLite.
    Returns metrics on matched startups and updated founder records.
    """
    enrichment_path = Path(enrichment_file)
    cache_path = Path(cache_dir)
    db_file = Path(db_path)

    if not enrichment_path.exists():
        return {"startups_matched": 0, "founders_updated": 0, "cache_files_updated": 0}

    payload = load_enrichment_payload(enrichment_path)
    if not payload:
        return {"startups_matched": 0, "founders_updated": 0, "cache_files_updated": 0}

    # Connect to SQLite
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query all entities
    cursor.execute("SELECT entity_id, canonical_name, normalized_domain, city, country_code FROM entities")
    entities = cursor.fetchall()

    entity_by_slug: Dict[str, Any] = {}
    for ent in entities:
        slug = slugify(ent["canonical_name"])
        entity_by_slug[slug] = ent
        if ent["normalized_domain"]:
            entity_by_slug[ent["normalized_domain"].lower()] = ent

    startups_matched = 0
    founders_updated = 0
    cache_files_updated = 0

    for item in payload:
        cname = item.get("company_name") or item.get("name") or item.get("canonical_name") or ""
        target_eid = item.get("entity_id")
        matched_ent = None

        if target_eid:
            matched_ent = next((e for e in entities if e["entity_id"] == target_eid), None)

        if not matched_ent and cname:
            candidate_names = [cname]
            if "/" in cname or "|" in cname:
                candidate_names.extend([p.strip() for p in re.split(r"[/|]", cname) if p.strip()])

            for cand_name in candidate_names:
                cslug = slugify(cand_name)
                matched_ent = entity_by_slug.get(cslug)
                if matched_ent:
                    break
                # Fuzzy match by startup token
                c_tokens = set(normalize_tokens(cand_name))
                for ent in entities:
                    ent_tokens = set(normalize_tokens(ent["canonical_name"]))
                    if c_tokens and ent_tokens and len(c_tokens & ent_tokens) >= 1 and (c_tokens.issubset(ent_tokens) or ent_tokens.issubset(c_tokens)):
                        matched_ent = ent
                        break
                if matched_ent:
                    break

        if not matched_ent:
            continue

        startups_matched += 1
        eid = matched_ent["entity_id"]

        # Verified city/country updates (preserving operational hubs from tax domiciles)
        current_city = matched_ent["city"]
        verified_city = normalize_city(item.get("verified_city") or item.get("city"))
        verified_country = (item.get("verified_country") or item.get("country_code") or "").strip().upper()

        effective_city = verified_city
        if current_city and current_city.lower() in OPERATIONAL_HUBS:
            if not verified_city or verified_city.lower() in TAX_DOMICILES:
                effective_city = current_city

        if effective_city or verified_country:
            cursor.execute(
                "UPDATE entities SET city = coalesce(?, city), country_code = coalesce(?, country_code) WHERE entity_id = ?",
                (effective_city or None, verified_country or None, eid)
            )

        # Merge into llm_cache file
        cache_file = cache_path / f"{eid}.json"
        cache_data = None
        cache_changed = False
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
            except Exception as e:
                logger.debug("Error reading cache file %s: %s", cache_file, e)

        if cache_data and effective_city:
            cache_city = cache_data.get("city")
            if not cache_city or (cache_city.lower() in TAX_DOMICILES and effective_city.lower() in OPERATIONAL_HUBS):
                cache_data["city"] = effective_city
                cache_changed = True
            elif cache_city.lower() not in OPERATIONAL_HUBS and effective_city.lower() not in TAX_DOMICILES:
                cache_data["city"] = effective_city
                cache_changed = True

        # Process founders
        f_list = item.get("founders", [])
        for f_entry in f_list:
            fname = f_entry.get("full_name") or f_entry.get("name")
            if not fname:
                continue

            raw_li = f_entry.get("linkedin_url") or f_entry.get("linkedin")
            raw_gh = f_entry.get("github_handle") or f_entry.get("github")

            valid_li = clean_url(raw_li) if (raw_li and is_valid_founder_linkedin(raw_li)) else None
            valid_gh = clean_github_handle(raw_gh)

            if not valid_li and not valid_gh:
                continue

            # 1. Update SQLite founders
            cursor.execute("""
                SELECT f.founder_id, f.full_name, f.linkedin_url, f.github_handle
                FROM founders f
                JOIN entity_founder_links l ON f.founder_id = l.founder_id
                WHERE l.entity_id = ?
            """, (eid,))
            db_founders = cursor.fetchall()

            matched_f_id = None
            for db_f in db_founders:
                if names_match(db_f["full_name"], fname):
                    matched_f_id = db_f["founder_id"]
                    break

            if matched_f_id:
                cursor.execute(
                    "UPDATE founders SET linkedin_url = coalesce(?, linkedin_url), github_handle = coalesce(?, github_handle) WHERE founder_id = ?",
                    (valid_li, valid_gh, matched_f_id)
                )
                founders_updated += 1
            else:
                # Insert founder and link
                new_f_id = generate_founder_id(full_name=fname, github_handle=valid_gh)
                cursor.execute(
                    "INSERT INTO founders (founder_id, full_name, linkedin_url, github_handle) VALUES (?, ?, ?, ?) ON CONFLICT(founder_id) DO UPDATE SET linkedin_url = coalesce(excluded.linkedin_url, founders.linkedin_url), github_handle = coalesce(excluded.github_handle, founders.github_handle)",
                    (new_f_id, fname, valid_li, valid_gh)
                )
                cursor.execute(
                    "INSERT OR IGNORE INTO entity_founder_links (entity_id, founder_id, link_method, link_confidence) VALUES (?, ?, 'gemini_deep_research', 0.90)",
                    (eid, new_f_id)
                )
                founders_updated += 1

            # 2. Update cache JSON
            if cache_data and "founders_enriched" in cache_data:
                founder_matched_in_cache = False
                for c_f in cache_data.get("founders_enriched", []):
                    if names_match(c_f.get("full_name", ""), fname):
                        if valid_li and not c_f.get("linkedin_url"):
                            c_f["linkedin_url"] = valid_li
                            cache_changed = True
                        if valid_gh and not c_f.get("github_handle"):
                            c_f["github_handle"] = valid_gh
                            cache_changed = True
                        founder_matched_in_cache = True
                        break

                if not founder_matched_in_cache and (valid_li or valid_gh):
                    cache_data["founders_enriched"].append({
                        "full_name": fname,
                        "role": f_entry.get("role", "Co-Founder"),
                        "linkedin_url": valid_li,
                        "github_handle": valid_gh,
                        "has_phd": bool(f_entry.get("has_phd", False)),
                        "universities": f_entry.get("universities", []),
                        "prior_companies": f_entry.get("prior_companies", []),
                        "is_ex_bigtech": bool(f_entry.get("is_ex_bigtech", False)),
                        "is_technical": bool(f_entry.get("is_technical", False)),
                        "source_of_identity": "gemini_deep_research"
                    })
                    cache_changed = True

        if cache_data and cache_changed:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, indent=2, ensure_ascii=False)
                cache_files_updated += 1
            except Exception as e:
                logger.warning("Could not write updated cache file %s: %s", cache_file, e)

    # Purge misattributed founders for NbMEA (Prof. Steinkasserer belongs to Mallia Innovations)
    nbmea_eid = "c0562771-1048-52bb-ba26-121814f6a6a9"
    cursor.execute("""
        SELECT f.founder_id FROM founders f
        JOIN entity_founder_links l ON f.founder_id = l.founder_id
        WHERE l.entity_id = ? AND (f.full_name LIKE '%Steinkasserer%' OR trim(f.full_name) = '')
    """, (nbmea_eid,))
    for (f_id_to_del,) in cursor.fetchall():
        cursor.execute("DELETE FROM entity_founder_links WHERE entity_id = ? AND founder_id = ?", (nbmea_eid, f_id_to_del))
        cursor.execute("SELECT count(*) FROM entity_founder_links WHERE founder_id = ?", (f_id_to_del,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("DELETE FROM founders WHERE founder_id = ?", (f_id_to_del,))

    nbmea_cache_file = cache_path / f"{nbmea_eid}.json"
    if nbmea_cache_file.exists():
        try:
            with open(nbmea_cache_file, "r", encoding="utf-8") as f:
                c_data = json.load(f)
            orig_founders = c_data.get("founders_enriched", [])
            cleaned_founders = [
                f for f in orig_founders
                if "steinkasserer" not in (f.get("full_name") or "").lower() and (f.get("full_name") or "").strip()
            ]
            if len(cleaned_founders) != len(orig_founders):
                c_data["founders_enriched"] = cleaned_founders
                with open(nbmea_cache_file, "w", encoding="utf-8") as f:
                    json.dump(c_data, f, indent=2, ensure_ascii=False)
                cache_files_updated += 1
        except Exception as e:
            logger.warning("Failed to purge Steinkasserer from %s: %s", nbmea_cache_file, e)

    conn.commit()
    conn.close()

    return {
        "startups_matched": startups_matched,
        "founders_updated": founders_updated,
        "cache_files_updated": cache_files_updated
    }
