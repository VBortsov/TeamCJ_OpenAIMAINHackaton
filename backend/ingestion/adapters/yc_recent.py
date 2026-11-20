"""
YC Recent (2025-2026) European Companies Signal Adapter.
Pulls fresh European YC companies (Batches 2025-2026) with accelerator='yc'
to supply real-world early-stage accelerator leads for ML model validation.
"""

from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional

from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain, normalize_city, slugify

logger = logging.getLogger(__name__)

YC_COMPANIES_URL = "https://yc-oss.github.io/api/companies/all.json"

# Country name to ISO-2 mapping
COUNTRY_MAP = {
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "switzerland": "CH",
    "schweiz": "CH",
    "austria": "AT",
    "österreich": "AT",
    "netherlands": "NL",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "denmark": "DK",
    "sweden": "SE",
    "norway": "NO",
    "finland": "FI",
    "spain": "ES",
    "italy": "IT",
    "belgium": "BE",
    "ireland": "IE"
}


class YcRecentAdapter(BaseSignalAdapter):
    """Fetches recent 2025-2026 European YC startups with accelerator='yc'."""

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="yc_recent")
        self.http_client = http_client

    async def fetch(self, limit: int = 50) -> List[RawCandidate]:
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None
        candidates: List[RawCandidate] = []

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Fetching recent 2025-2026 YC European cohort...")
            companies = await client.get_json(YC_COMPANIES_URL, use_cache=True)
            if not isinstance(companies, list):
                logger.warning("Unexpected response format from YC API: expected list, got %s", type(companies))
                return []

            for c in companies:
                if len(candidates) >= limit:
                    break

                batch = c.get("batch", "")
                # Check for 2025 or 2026 batches
                year_match = re.search(r"20(\d{2})", batch)
                year = int(f"20{year_match.group(1)}") if year_match else None
                if not year:
                    short_match = re.search(r"\b[WSws](\d{2})\b", batch)
                    if short_match:
                        year = int(f"20{short_match.group(1)}")

                if not year or year < 2025:
                    continue

                all_loc = c.get("all_locations") or ""
                regions = " ".join(c.get("regions") or [])
                loc_text = f"{all_loc} {regions}".lower()

                country_code = None
                for cname, code in COUNTRY_MAP.items():
                    if re.search(rf"\b{re.escape(cname)}\b", loc_text):
                        country_code = code
                        break

                if not country_code:
                    continue

                name = c.get("name", "").strip()
                if not name:
                    continue

                website = c.get("website") or ""
                norm_domain = normalize_domain(website)
                city = None
                if all_loc:
                    city_part = all_loc.split(",")[0].strip()
                    city = normalize_city(city_part)

                desc = c.get("one_liner") or c.get("long_description") or f"YC {batch} European venture."
                tags = ["YC", f"YC {batch}"]
                if c.get("industry"):
                    tags.append(c["industry"])
                if c.get("subindustry"):
                    tags.append(c["subindustry"])

                slug = c.get("slug") or slugify(name)
                yc_url = c.get("url") or f"https://www.ycombinator.com/companies/{slug}"

                candidate = RawCandidate(
                    canonical_name=name,
                    normalized_domain=norm_domain,
                    website_url=website or yc_url,
                    country_code=country_code,
                    city=city,
                    accelerator="yc",
                    description=desc,
                    raw_tags=tags,
                    source_name="yc_recent",
                    source_native_id=slug,
                    signals=[
                        RawSignal(
                            source_name="yc_recent",
                            source_native_id=slug,
                            signal_date=f"{year}-01-01",
                            source_url=yc_url,
                            summary=f"YC {batch} European Startup ({all_loc})",
                            raw_metrics={
                                "batch": batch,
                                "batch_year": year,
                                "team_size": c.get("team_size", 2),
                                "location": all_loc,
                                "status": c.get("status", "Active")
                            }
                        )
                    ]
                )
                candidates.append(candidate)

            logger.info("Ingested %d recent 2025-2026 European YC startups", len(candidates))

        except Exception as e:
            logger.error("Failed fetching recent YC companies: %s", e)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates
