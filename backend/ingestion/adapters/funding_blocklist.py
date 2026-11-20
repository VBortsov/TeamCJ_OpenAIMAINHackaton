"""
Funding Blocklist Ingestion & Exclusion Index.
Aggregates known funded scaleups, Series A/B/C announcements, and older YC companies
to populate the SQLite funding_blocklist table and guarantee early-stage HTGF mandate compliance.
"""

from __future__ import annotations
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import settings
from backend.db.repository import get_db_connection, normalize_domain, slugify
from backend.ingestion.http_client import CachedHttpClient

logger = logging.getLogger(__name__)

YC_COMPANIES_URL = "https://yc-oss.github.io/api/companies/all.json"
RSS_FEEDS = [
    ("tech_eu", "https://tech.eu/feed/"),
    ("silicon_canals", "https://siliconcanals.com/feed/"),
    ("eu_startups", "https://www.eu-startups.com/feed/"),
]

# Curated High-Profile German / European Scaleups (Already raised Series A+ / Mega-Rounds)
CURATED_FUNDED_SCALEUPS = [
    ("Tacto", "tacto.ai", "Series A ($50M+)", "https://tacto.ai"),
    ("Celonis", "celonis.com", "Series D / Decacorn", "https://celonis.com"),
    ("Helsing", "helsing.ai", "Series B/C (€450M)", "https://helsing.ai"),
    ("DeepL", "deepl.com", "Series B / Unicorn", "https://deepl.com"),
    ("Personio", "personio.com", "Series E / Unicorn", "https://personio.com"),
    ("Aleph Alpha", "aleph-alpha.com", "Series B (€500M)", "https://aleph-alpha.com"),
    ("Flix", "flixbus.com", "Pre-IPO / Series F", "https://flixbus.com"),
    ("Trade Republic", "traderepublic.com", "Series C (€900M)", "https://traderepublic.com"),
    ("Enpal", "enpal.de", "Series D (€400M)", "https://enpal.de"),
    ("Wefox", "wefox.com", "Series D", "https://wefox.com"),
    ("N26", "n26.com", "Series E / Unicorn", "https://n26.com"),
    ("Lilium", "lilium.com", "Public / Post-IPO", "https://lilium.com"),
    ("Volocopter", "volocopter.com", "Series E", "https://volocopter.com"),
    ("Mistral AI", "mistral.ai", "Series A/B (€600M)", "https://mistral.ai"),
    ("Poolside", "poolside.ai", "Series B ($500M)", "https://poolside.ai"),
    ("Lightricks", "lightricks.com", "Series D ($300M+)", "https://lightricks.com"),
    ("LTX", "ltx.io", "Late-stage / Lightricks product", "https://ltx.io"),
]


class FundingBlocklistAdapter:
    """Manages ingestion of funded / scaleup entities into the funding_blocklist table."""

    def __init__(self, db_path: Optional[Path | str] = None, http_client: Optional[CachedHttpClient] = None):
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.http_client = http_client

    async def sync_blocklist(self) -> int:
        """Fetch and populate all funding blocklist sources."""
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None
        total_inserted = 0

        try:
            if own_client:
                await client.__aenter__()

            entries: List[Tuple[str, Optional[str], str, str, str]] = []

            # 1. Add curated German scaleups
            for name, domain, round_info, url in CURATED_FUNDED_SCALEUPS:
                entries.append((
                    slugify(name),
                    normalize_domain(domain),
                    "curated_scaleup",
                    round_info,
                    url
                ))

            # 2. Add YC Companies <= Batch 2024
            yc_entries = await self._fetch_yc_companies(client)
            entries.extend(yc_entries)

            # 3. Add RSS Feed funding rounds
            rss_entries = await self._fetch_rss_funding(client)
            entries.extend(rss_entries)

            # Persist to database
            with get_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                sql = """
                INSERT OR REPLACE INTO funding_blocklist (
                    normalized_name, normalized_domain, source, round_info, source_url
                ) VALUES (?, ?, ?, ?, ?)
                """
                for item in entries:
                    if not item[0]:
                        continue
                    cursor.execute(sql, item)
                    total_inserted += 1
                conn.commit()

            logger.info("Synchronized %d funding blocklist records in SQLite", total_inserted)

        except Exception as e:
            logger.error("Error synchronizing funding blocklist: %s", e)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return total_inserted

    async def _fetch_yc_companies(self, client: CachedHttpClient) -> List[Tuple[str, Optional[str], str, str, str]]:
        entries = []
        try:
            logger.info("Fetching YC companies dataset...")
            companies = await client.get_json(YC_COMPANIES_URL, use_cache=True)
            for c in companies:
                batch = c.get("batch", "")
                # Extract year from batch string like "Winter 2022" or "S23"
                year_match = re.search(r"20(\d{2})", batch)
                year = int(f"20{year_match.group(1)}") if year_match else None
                if not year:
                    short_match = re.search(r"\b[WSws](\d{2})\b", batch)
                    if short_match:
                        year = int(f"20{short_match.group(1)}")

                # Block any YC company from 2024 or earlier
                if year and year <= 2024:
                    name = c.get("name", "")
                    website = c.get("website", "")
                    entries.append((
                        slugify(name),
                        normalize_domain(website),
                        "yc_oss",
                        f"YC {batch}",
                        c.get("url") or website or YC_COMPANIES_URL
                    ))
            logger.info("Identified %d historical YC companies for blocklist", len(entries))
        except Exception as e:
            logger.warning("Could not fetch YC companies (continuing): %s", e)
        return entries

    async def _fetch_rss_funding(self, client: CachedHttpClient) -> List[Tuple[str, Optional[str], str, str, str]]:
        entries = []
        funding_patterns = [
            re.compile(r"([A-Z][a-zA-Z0-9_\-\s]{2,25})\s+(?:raises|secures|bags|closes|nabs|lands|raises\s+a)\s+([€$£\d\.]+\s*(?:M|million|B|billion|k)?)", re.IGNORECASE),
            re.compile(r"([A-Z][a-zA-Z0-9_\-\s]{2,25})\s+(?:raises|secures|bags|closes)\s+(Series\s+[A-Z])", re.IGNORECASE),
        ]

        for source_key, feed_url in RSS_FEEDS:
            try:
                logger.info("Parsing RSS feed from %s...", feed_url)
                res = await client.get(feed_url, use_cache=True)
                if res.status_code != 200:
                    continue

                root = ET.fromstring(res.content)
                channel = root.find("channel")
                items = channel.findall("item") if channel is not None else []

                for it in items:
                    title = it.findtext("title") or ""
                    link = it.findtext("link") or feed_url

                    for pat in funding_patterns:
                        m = pat.search(title)
                        if m:
                            cname = m.group(1).strip()
                            round_desc = m.group(2).strip()
                            # Exclude noise words
                            if any(w in cname.lower() for w in ("how", "why", "the", "europe", "startup", "eu", "germany", "ai")):
                                continue
                            entries.append((
                                slugify(cname),
                                None,
                                source_key,
                                f"Funding Round: {round_desc}",
                                link
                            ))
                            break
            except Exception as e:
                logger.debug("Failed reading RSS feed %s: %s", feed_url, e)

        logger.info("Extracted %d funded companies from European tech feeds", len(entries))
        return entries
