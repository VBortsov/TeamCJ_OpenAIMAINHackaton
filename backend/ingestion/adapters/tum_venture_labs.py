"""
TUM Venture Labs Signal Adapter.
Scrapes domain-specific incubators and spin-outs across Technical University of Munich
and UnternehmerTUM (AI, Robotics, Quantum, Aerospace, Healthcare, etc.).
Also captures university lab events for the European events sourcing catalog.
"""

from __future__ import annotations
import html
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.ingestion.adapters.eth_rwth import clean_legal_name
from backend.db.repository import normalize_domain, slugify

logger = logging.getLogger(__name__)

TUM_BASE_URL = "https://www.tum-venture-labs.de"
TUM_EVENTS_URL = "https://www.tum-venture-labs.de/events/"

LAB_DOMAINS: List[Tuple[str, str]] = [
    ("software-ai", "Software & AI"),
    ("robotics-ai", "Robotics & AI"),
    ("quantum", "Quantum"),
    ("aerospace-defense", "Aerospace & Defense"),
    ("healthcare", "Healthcare"),
    ("built-environment", "Built Environment"),
    ("additive-manufacturing", "Additive Manufacturing"),
    ("chem-biosystems", "Chem & Biosystems"),
    ("climate-circular", "Climate & Circular"),
    ("food-agro-biotech", "Food, Agro & Biotech"),
    ("mobility", "Mobility"),
]


class TumVentureLabsAdapter(BaseSignalAdapter):
    """
    Adapter for TUM Venture Labs (TUM & UnternehmerTUM).
    Discovers deeptech spin-outs across 11 domain hubs in Munich.
    """

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="tum_venture_labs")
        self.http_client = http_client

    async def fetch(self, limit: int = 50) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Scanning TUM Venture Labs domain hubs...")
            discovered_teams: Dict[str, Tuple[str, str, str]] = {}  # slug -> (domain_label, team_url, lab_url)

            # 1. Discover all team slugs from domain lab landing pages
            for slug, label in LAB_DOMAINS:
                lab_url = f"{TUM_BASE_URL}/labs/{slug}/"
                try:
                    html_text = await client.get_text(lab_url, use_cache=True)
                    soup = BeautifulSoup(html_text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if "/teams/" in href:
                            t_slug = href.strip("/").split("/")[-1].lower()
                            if t_slug and t_slug not in discovered_teams:
                                full_team_url = urljoin(TUM_BASE_URL, href)
                                discovered_teams[t_slug] = (label, full_team_url, lab_url)
                except Exception as ex:
                    logger.warning("Failed scanning TUM lab %s: %s", slug, ex)

            logger.info("Discovered %d distinct TUM Venture Labs teams across hubs", len(discovered_teams))

            # 2. Ingest team profiles
            seen_names: Set[str] = set()

            for t_slug, (domain_label, team_url, lab_url) in discovered_teams.items():
                if len(candidates) >= limit:
                    break

                try:
                    page_html = await client.get_text(team_url, use_cache=True)
                    soup = BeautifulSoup(page_html, "html.parser")

                    # Extract company name
                    h1 = soup.find("h1")
                    raw_name = h1.get_text(strip=True) if h1 else t_slug.replace("-", " ").title()
                    cname, legal_name, legal_form = clean_legal_name(raw_name)

                    norm_key = slugify(cname)
                    if norm_key in seen_names or not cname or len(cname) < 2:
                        continue
                    seen_names.add(norm_key)

                    # Extract description
                    paragraphs = [
                        p.get_text(strip=True)
                        for p in soup.find_all("p")
                        if len(p.get_text(strip=True)) > 30 and not any(skip in p.get_text(strip=True).lower() for skip in ["cookies", "legal notice", "privacy policy"])
                    ]
                    desc = " ".join(paragraphs[:2]) if paragraphs else f"TUM Venture Labs {domain_label} spin-off venture."

                    # Extract external website if linked
                    website_url = None
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if href.startswith("http") and not any(host in href for host in ["tum-venture-labs.de", "tum.de", "linkedin.com", "spotify.com", "instagram.com", "twitter.com", "x.com"]):
                            website_url = href
                            break

                    norm_domain = normalize_domain(website_url) if website_url else None

                    signal = RawSignal(
                        source_name=self.name,
                        source_native_id=f"tum:{norm_key}",
                        signal_date="2026-01-01",
                        source_url=team_url,
                        summary=f"TUM Venture Labs ({domain_label}) spin-off: {cname}",
                        raw_metrics={
                            "domain": domain_label,
                            "university": "Technical University of Munich",
                            "accelerator": "TUM Venture Labs / UnternehmerTUM",
                            "city": "Munich"
                        }
                    )

                    candidate = RawCandidate(
                        canonical_name=cname,
                        normalized_domain=norm_domain,
                        website_url=website_url or team_url,
                        legal_name=legal_name,
                        legal_form=legal_form,
                        country_code="DE",
                        city="Munich",
                        accelerator="tum_venture_labs",
                        description=desc,
                        raw_tags=["TUM Venture Labs", "DeepTech", "Academic Spin-off", "Munich", domain_label],
                        funding_status="unknown",
                        source_name=self.name,
                        source_native_id=signal.source_native_id,
                        signals=[signal]
                    )
                    candidates.append(candidate)

                except Exception as ex:
                    logger.warning("Failed parsing TUM team %s: %s", t_slug, ex)

            logger.info("Ingested %d spin-off ventures from TUM Venture Labs", len(candidates))

        except Exception as e:
            logger.error("Failed fetching TUM Venture Labs teams: %s", e, exc_info=True)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]

    async def fetch_events(self) -> List[Dict[str, Any]]:
        """Scrapes official TUM Venture Labs upcoming ecosystem events."""
        events: List[Dict[str, Any]] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Scraping TUM Venture Labs upcoming events...")
            html_text = await client.get_text(TUM_EVENTS_URL, use_cache=True)
            soup = BeautifulSoup(html_text, "html.parser")

            seen_urls: Set[str] = set()
            for c in soup.find_all("div", class_=lambda x: x and ("card" in x or "item" in x or "event" in x)):
                a = c.find("a", href=True)
                text = c.get_text(strip=True)
                if not a or not text or len(text) < 15:
                    continue

                title = None
                h = c.find(["h3", "h4", "h5", "strong"])
                if h:
                    title = h.get_text(strip=True)

                date_match = re.search(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?,?\s*([A-Za-z]+ \d{1,2}, \d{4})", text)
                date_str = date_match.group(1) if date_match else "2026-12-01"

                full_url = urljoin(TUM_BASE_URL, a["href"])
                if full_url in seen_urls or not title:
                    continue
                seen_urls.add(full_url)

                events.append({
                    "type": "lab",
                    "name": title,
                    "organiser": "TUM Venture Labs",
                    "location": "Munich",
                    "country_code": "DE",
                    "date": date_str,
                    "url": full_url,
                    "description": "TUM Venture Labs deeptech & founder ecosystem event in Munich.",
                    "projects": []
                })

            logger.info("Discovered %d TUM Venture Labs upcoming events", len(events))
        except Exception as ex:
            logger.warning("Failed fetching TUM Venture Labs events: %s", ex)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return events
