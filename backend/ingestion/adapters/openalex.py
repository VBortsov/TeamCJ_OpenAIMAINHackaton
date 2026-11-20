"""
Adapter for OpenAlex: Ingests EU research lab -> industry co-affiliations.
Uses strictly cost-efficient filtered list queries ($0.0001/call) without expensive search.
"""

from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from backend.config import settings, BIG_TECH_BLOCKLIST, OPENALEX_PRIMARY_LAB_IDS
from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"

# Expanded list of corporate / mega-cap entities to filter out
EXTENDED_BIG_CORP_BLOCKLIST: Set[str] = BIG_TECH_BLOCKLIST | {
    "eli lilly", "lilly", "novo nordisk", "astrazeneca", "gsk", "glaxosmithkline",
    "pfizer", "moderna", "sanofi", "novartis", "roche", "bayer", "basf", "merck",
    "verizon", "at&t", "vodafone", "telefonica", "orange", "deutsche telekom",
    "lockheed", "boeing", "raytheon", "airbus", "general electric", "ge healthcare",
    "honda", "toyota", "ford", "general motors", "stellantis", "renault",
    "mckinsey", "boston consulting", "bcg", "bain & company", "deloitte", "pwc", "kpmg", "ey",
    "accenture", "capgemini", "infosys", "tcs", "wipro", "cognizant",
    "goldman sachs", "jpmorgan", "morgan stanley", "ubs", "credit suisse", "deutsche bank",
    "bnp paribas", "santander", "barclays", "hsbc", "citigroup", "blackrock"
}


def is_corporate_or_big_tech(name: str) -> bool:
    """Check if the organization name matches known big tech / conglomerates."""
    norm = re.sub(r"[^\w\s]", " ", name.lower()).strip()
    tokens = set(norm.split())

    for blocked in EXTENDED_BIG_CORP_BLOCKLIST:
        if blocked in norm or blocked in tokens:
            return True
    return False


def clean_company_name(raw_name: str) -> str:
    """Strip country designations in parentheses like 'ATG Europe (Netherlands)'."""
    cleaned = re.sub(r"\s*\([^)]*\)", "", raw_name).strip()
    return cleaned


class OpenAlexAdapter(BaseSignalAdapter):
    """
    OpenAlex API Adapter to discover early-stage DeepTech startups co-authoring
    cutting-edge papers with DACH/EU elite labs.
    """

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="openalex")
        self.http_client = http_client

    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        # Prepare lab lineage filter
        lab_filter = "|".join(OPENALEX_PRIMARY_LAB_IDS)
        filter_param = (
            f"authorships.institutions.lineage:{lab_filter},"
            "from_publication_date:2025-01-01,"
            "authorships.institutions.type:company"
        )
        select_fields = (
            "id,doi,title,publication_date,cited_by_count,authorships,primary_topic,awards"
        )
        params = {
            "filter": filter_param,
            "select": select_fields,
            "per_page": 100,
            "sort": "publication_date:desc"
        }

        headers = {}
        if settings.OPENALEX_API_KEY:
            headers["Authorization"] = f"Bearer {settings.OPENALEX_API_KEY}"

        seen_companies: Set[str] = set()

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Querying OpenAlex filtered list (publication_date >= 2025-01-01)...")
            res_json = await client.get_json(
                OPENALEX_WORKS_URL,
                params=params,
                headers=headers or None,
                use_cache=True
            )

            works = res_json.get("results", [])
            logger.info("Retrieved %d OpenAlex works with university-company co-authorships", len(works))

            for work in works:
                if len(candidates) >= limit:
                    break

                work_id = work.get("id")
                work_doi = work.get("doi") or work_id
                work_title = work.get("title") or "Untitled Publication"
                pub_date = work.get("publication_date")
                cited_by = work.get("cited_by_count", 0)
                topic_info = work.get("primary_topic") or {}
                topic_name = topic_info.get("display_name", "DeepTech")

                authorships = work.get("authorships", [])

                # Identify company institutions and university researchers
                company_insts: List[Dict[str, Any]] = []
                academic_researchers: List[RawFounder] = []

                for auth in authorships:
                    author_meta = auth.get("author") or {}
                    author_name = author_meta.get("display_name")
                    author_id = author_meta.get("id")
                    author_orcid = author_meta.get("orcid")

                    institutions = auth.get("institutions") or []
                    for inst in institutions:
                        inst_type = inst.get("type")
                        inst_name = inst.get("display_name")

                        if inst_type == "company" and inst_name:
                            if not is_corporate_or_big_tech(inst_name):
                                company_insts.append(inst)
                        elif inst_type in ("education", "facility", "nonprofit") and author_name:
                            # Record elite academic author
                            inst_display = inst.get("display_name", "Elite EU Lab")
                            founder = RawFounder(
                                full_name=author_name,
                                openalex_author_id=author_id,
                                orcid_id=author_orcid,
                                elite_affiliations=[inst_display],
                                link_method="paper_coauthorship",
                                link_confidence=0.85
                            )
                            academic_researchers.append(founder)

                # For each valid non-bigtech startup company found on the paper
                for c_inst in company_insts:
                    raw_cname = c_inst.get("display_name", "")
                    clean_name = clean_company_name(raw_cname)
                    c_id = c_inst.get("id", "")
                    country_code = c_inst.get("country_code")
                    ror = c_inst.get("ror")

                    # Normalize key
                    norm_key = clean_name.lower().strip()
                    if norm_key in seen_companies or not clean_name:
                        continue
                    seen_companies.add(norm_key)

                    signal = RawSignal(
                        source_name=self.name,
                        source_native_id=f"openalex:{work_id.split('/')[-1]}:{c_id.split('/')[-1]}",
                        signal_date=pub_date,
                        raw_metrics={
                            "work_id": work_id,
                            "doi": work_doi,
                            "paper_title": work_title,
                            "cited_by_count": cited_by,
                            "primary_topic": topic_name,
                            "institution_openalex_id": c_id,
                            "ror": ror
                        },
                        source_url=work_doi if work_doi.startswith("http") else f"https://openalex.org/{work_id.split('/')[-1]}"
                    )

                    candidate = RawCandidate(
                        canonical_name=clean_name,
                        legal_name=raw_cname,
                        country_code=country_code,
                        description=(
                            f"Early-stage venture co-authoring scientific research on '{work_title}' "
                            f"in topic area '{topic_name}' with elite EU researchers."
                        ),
                        raw_tags=["DeepTech", "Research Co-Affiliation", "Academic Spin-off", topic_name],
                        funding_status="unknown",
                        source_name=self.name,
                        source_native_id=signal.source_native_id,
                        signals=[signal],
                        founders=academic_researchers[:3]  # attach top academic co-authors
                    )
                    candidates.append(candidate)

                    if len(candidates) >= limit:
                        break

        except Exception as e:
            logger.error("Failed fetching OpenAlex works: %s", e, exc_info=True)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]
