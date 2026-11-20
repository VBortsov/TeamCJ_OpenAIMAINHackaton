"""
EU CORDIS Horizon Europe ERC Proof-of-Concept Adapter.
Extracts ultra-early, pre-incorporation DeepTech projects hosted at German & DACH institutions.
"""

from __future__ import annotations
import csv
import io
import logging
from pathlib import Path
from typing import List, Optional
import zipfile

from backend.config import settings
from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain

logger = logging.getLogger(__name__)

CORDIS_HORIZON_ZIP_URL = "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip"


class CordisErcAdapter(BaseSignalAdapter):
    """
    Ingests ERC Proof of Concept (PoC) grants under Horizon Europe.
    Provides ultra-early academic DeepTech spin-off leads before formal incorporation.
    """

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="cordis_erc")
        self.http_client = http_client
        self.cache_dir = settings.DATA_DIR / "cordis_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.zip_file_path = self.cache_dir / "cordis-HORIZONprojects-csv.zip"

    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        try:
            if own_client:
                await client.__aenter__()

            # Check if cached zip exists on disk
            if not self.zip_file_path.exists() or self.zip_file_path.stat().st_size < 1000:
                logger.info("Downloading Horizon Europe projects archive from CORDIS...")
                res = await client.get(CORDIS_HORIZON_ZIP_URL, use_cache=True)
                with open(self.zip_file_path, "wb") as f:
                    f.write(res.content)
                logger.info("Saved CORDIS projects archive (%d bytes)", len(res.content))

            # Read ZIP archive
            with zipfile.ZipFile(self.zip_file_path, "r") as z:
                # 1. Parse German & DACH host organizations
                coord_orgs = {}
                with z.open("organization.csv") as f_org:
                    for row in csv.DictReader(io.TextIOWrapper(f_org, encoding="utf-8"), delimiter=";"):
                        role = row.get("role", "")
                        country = row.get("country", "")
                        if role == "coordinator" and country in ("DE", "CH", "AT", "NL"):
                            pid = row.get("projectID")
                            coord_orgs[pid] = {
                                "name": row.get("name", "Host Lab"),
                                "city": row.get("city"),
                                "country": country,
                                "url": row.get("organizationURL")
                            }

                # 2. Parse Projects matching ERC-POC
                project_records = []
                with z.open("project.csv") as f_proj:
                    for row in csv.DictReader(io.TextIOWrapper(f_proj, encoding="utf-8"), delimiter=";"):
                        pid = row.get("id")
                        if pid in coord_orgs:
                            fs = row.get("fundingScheme", "")
                            sc = row.get("subCall", "")
                            start_date = row.get("startDate", "")
                            if "POC" in fs or "POC" in sc:
                                project_records.append((row, coord_orgs[pid]))

                # Sort projects by startDate descending (most recent 2024-2026 first)
                project_records.sort(key=lambda item: item[0].get("startDate", ""), reverse=True)
                logger.info("Found %d DACH/EU ERC-POC projects in CORDIS", len(project_records))

                for proj, host_info in project_records[:limit]:
                    acronym = proj.get("acronym") or f"ERC-POC-{proj.get('id')}"
                    title = proj.get("title", "")
                    objective = proj.get("objective", "")
                    start_date = proj.get("startDate")
                    pid = proj.get("id")
                    grant_doi = proj.get("grantDoi")
                    country = host_info.get("country", "DE")
                    host_name = host_info.get("name")
                    city = host_info.get("city")
                    host_url = host_info.get("url")

                    source_url = grant_doi if grant_doi and grant_doi.startswith("http") else f"https://cordis.europa.eu/project/id/{pid}"

                    signal = RawSignal(
                        source_name=self.name,
                        source_native_id=f"cordis:poc:{pid}",
                        signal_date=start_date,
                        raw_metrics={
                            "project_id": pid,
                            "acronym": acronym,
                            "call": proj.get("subCall") or proj.get("masterCall"),
                            "funding_scheme": proj.get("fundingScheme"),
                            "ec_max_contribution": proj.get("ecMaxContribution"),
                            "host_institution": host_name,
                            "city": city,
                        },
                        source_url=source_url
                    )

                    candidate = RawCandidate(
                        canonical_name=acronym,
                        legal_name=f"{acronym} (ERC Proof of Concept, {host_name})",
                        country_code=country,
                        city=city,
                        description=f"ERC Proof-of-Concept Project: {title}. Host: {host_name} ({city}). Objective: {objective[:200]}...",
                        raw_tags=["DeepTech", "ERC Proof-of-Concept", "Horizon Europe", "Pre-Incorporation", "Academic IP"],
                        funding_status="grant_backed",
                        source_name=self.name,
                        source_native_id=signal.source_native_id,
                        signals=[signal],
                        founders=[]
                    )
                    candidates.append(candidate)

        except Exception as e:
            logger.warning("CORDIS adapter encountered issue (recovering gracefully): %s", e)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]
