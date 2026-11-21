"""
BayStartUP Bavarian Business Plan Competitions Signal Adapter.
Scrapes official winner and finalist announcements for Munich, Northern Bavaria,
Swabia, and Bavarian Idea competitions (2025-2026 cohorts).
"""

from __future__ import annotations
import html
import logging
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.ingestion.adapters.eth_rwth import clean_legal_name
from backend.db.repository import normalize_domain, normalize_city, slugify

logger = logging.getLogger(__name__)

BAYSTARTUP_SIEGER_URL = "https://www.baystartup.de/businessplan-wettbewerbe/sieger"

CURATED_ARTICLES = [
    ("Münchener Businessplan Wettbewerb 2026", "Munich", 2026, "https://www.baystartup.de/startupdate/das-sind-die-sieger-im-muenchner-businessplan-wettbewerb-2026"),
    ("Businessplan Wettbewerb Nordbayern 2026", "Nuremberg", 2026, "https://www.baystartup.de/startupdate/nordbayerns-top-startups-das-sind-die-sieger-im-businessplan-wettbewerb-2026"),
    ("Businessplan Wettbewerb Schwaben 2026", "Augsburg", 2026, "https://www.baystartup.de/startupdate/sieger-im-businessplan-wettbewerb-schwaben-2026"),
    ("Businessplan Wettbewerb Ideenreich 2026", "Regensburg", 2026, "https://www.baystartup.de/startupdate/sieger-im-businessplan-wettbewerb-ideenreich-2026"),
    ("Münchener Businessplan Wettbewerb 2026 Finale", "Munich", 2026, "https://www.baystartup.de/startupdate/acht-startups-im-finale-des-muenchner-businessplan-wettbewerbs-2026"),
    ("Female Founders Contest Bayern 2026", "Munich", 2026, "https://www.baystartup.de/startupdate/female-founders-contest-2026-das-sind-die-siegerinnen-des-bayerischen-finales"),
    ("Münchener Businessplan Wettbewerb 2025", "Munich", 2025, "https://www.baystartup.de/startupdate/die-sieger-im-muenchener-businessplan-wettbewerb-2025"),
    ("Businessplan Wettbewerb Nordbayern 2025", "Nuremberg", 2025, "https://www.baystartup.de/startupdate/das-sind-die-sieger-im-businessplan-wettbewerb-nordbayern-2025"),
    ("Businessplan Wettbewerb Schwaben 2025", "Augsburg", 2025, "https://www.baystartup.de/startupdate/sieger-im-businessplan-wettbewerb-schwaben-2025"),
    ("Businessplan Wettbewerb Ideenreich 2025", "Regensburg", 2025, "https://www.baystartup.de/startupdate/sieger-im-businessplan-wettbewerb-ideenreich-2025"),
]

CITY_HINTS = {
    "münchen": "Munich",
    "munich": "Munich",
    "nürnberg": "Nuremberg",
    "nuernberg": "Nuremberg",
    "nuremberg": "Nuremberg",
    "augsburg": "Augsburg",
    "regensburg": "Regensburg",
    "würzburg": "Würzburg",
    "erlangen": "Erlangen",
    "bayreuth": "Bayreuth",
    "kempten": "Kempten",
    "deggendorf": "Deggendorf",
    "landshut": "Landshut",
    "passau": "Passau",
    "ingolstadt": "Ingolstadt",
    "rosenheim": "Rosenheim",
    "bamberg": "Bamberg",
    "ansbach": "Ansbach",
}


def clean_baystartup_company_name(raw: str) -> str:
    """Clean raw heading text to isolate pure company brand name."""
    name = html.unescape(raw).strip()
    # Remove phrases like '– KI macht kollektive Stimmungen messbar' or ': ...'
    name = re.split(r"[\u2013\u2014–—:]", name)[0].strip()
    # Remove geographic suffixes like 'aus München', 'von der Universität ...', 'vom ITC1 ...'
    name = re.sub(r"\s+(?:aus|vom|von der|von)\s+.*$", "", name, flags=re.IGNORECASE).strip()
    canonical, _, _ = clean_legal_name(name)
    return canonical.strip()


class BayStartUPAdapter(BaseSignalAdapter):
    """
    Adapter for BayStartUP: Bavarian Business Plan Competitions.
    Identifies elite university spin-offs, hardware & DeepTech winners across Bavaria.
    """

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="baystartup")
        self.http_client = http_client

    async def fetch(self, limit: int = 50) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Discovering BayStartUP 2025-2026 winner announcements...")
            
            # 1. Discover dynamically from overview page
            articles_to_process = list(CURATED_ARTICLES)
            try:
                overview_html = await client.get_text(BAYSTARTUP_SIEGER_URL, use_cache=True)
                soup = BeautifulSoup(overview_html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/startupdate/" in href and any(k in href for k in ["sieger", "gewinn", "wettbewerb"]):
                        full_url = urljoin(BAYSTARTUP_SIEGER_URL, href)
                        if not any(art[3] == full_url for art in articles_to_process):
                            year = 2026 if "2026" in href else (2025 if "2025" in href else 2026)
                            city = "Munich" if "muench" in href else ("Nuremberg" if "nordbayern" in href else ("Augsburg" if "schwaben" in href else "Regensburg"))
                            articles_to_process.append(("Bavarian Businessplan Competition", city, year, full_url))
            except Exception as ex:
                logger.warning("Failed scraping BayStartUP overview: %s", ex)

            seen_names: Set[str] = set()

            for comp_name, default_city, year, url in articles_to_process:
                if len(candidates) >= limit:
                    break

                try:
                    html_text = await client.get_text(url, use_cache=True)
                    soup = BeautifulSoup(html_text, "html.parser")

                    # Scan for place rankings: 'Platz 1: Foo' or '1. Platz: Bar'
                    elements = soup.find_all(["h2", "h3", "h4", "p", "strong"])
                    for el in elements:
                        if len(candidates) >= limit:
                            break

                        text = el.get_text(strip=True)
                        m = re.search(r"(?:Platz\s*(\d+)|\b(\d+)\.\s*Platz)\s*:\s*([^–—\n]+)", text, re.IGNORECASE)
                        if not m:
                            continue

                        rank = m.group(1) or m.group(2) or "Winner"
                        raw_name = m.group(3).strip()
                        cname = clean_baystartup_company_name(raw_name)

                        if len(cname) < 2 or cname.lower() in ("platz", "sieger", "die sieger", "gewinner"):
                            continue

                        norm_key = slugify(cname)
                        if norm_key in seen_names:
                            continue
                        seen_names.add(norm_key)

                        # Find description in sibling paragraphs
                        desc = ""
                        curr = el.next_sibling
                        while curr and not desc:
                            if hasattr(curr, "get_text"):
                                t = curr.get_text(strip=True)
                                if len(t) > 25 and "Platz" not in t and "Sponsoren" not in t:
                                    desc = t
                            curr = curr.next_sibling

                        if not desc:
                            p_next = el.find_next("p")
                            if p_next and p_next != el:
                                t = p_next.get_text(strip=True)
                                if len(t) > 25:
                                    desc = t

                        # Detect city
                        detected_city = default_city
                        desc_lower = desc.lower()
                        for hint_word, c_std in CITY_HINTS.items():
                            if re.search(rf"\b{re.escape(hint_word)}\b", desc_lower) or re.search(rf"\b{re.escape(hint_word)}\b", text.lower()):
                                detected_city = c_std
                                break

                        # Check for external website link inside or near
                        website_url = None
                        link_el = el.find("a", href=True) or (el.find_next("a", href=True) if el.find_next("a", href=True) else None)
                        if link_el and link_el.get("href"):
                            href = link_el["href"]
                            if href.startswith("http") and not any(p in href for p in ["baystartup.de", "ey.com"]):
                                website_url = href

                        norm_domain = normalize_domain(website_url) if website_url else None
                        if not norm_domain:
                            website_url = None
                        clean_name, legal_name, legal_form = clean_legal_name(cname)

                        signal = RawSignal(
                            source_name=self.name,
                            source_native_id=f"baystartup:{year}:{norm_key}",
                            signal_date=f"{year}-01-01",
                            source_url=url,
                            summary=f"{comp_name} - Rank {rank}: {clean_name}",
                            raw_metrics={
                                "competition": comp_name,
                                "rank": rank,
                                "year": year,
                                "city": detected_city,
                                "organizer": "BayStartUP GmbH"
                            }
                        )

                        candidate = RawCandidate(
                            canonical_name=clean_name,
                            normalized_domain=norm_domain,
                            website_url=website_url,
                            legal_name=legal_name,
                            legal_form=legal_form,
                            country_code="DE",
                            city=detected_city,
                            accelerator="baystartup",
                            description=desc or f"{comp_name} ({year}) Rank {rank} startup based in {detected_city}.",
                            raw_tags=["BayStartUP", "Bavarian Business Plan Award", "DeepTech", "Startup Award", comp_name],
                            funding_status="unknown",
                            source_name=self.name,
                            source_native_id=signal.source_native_id,
                            signals=[signal]
                        )
                        candidates.append(candidate)

                except Exception as ex:
                    logger.warning("Error scraping BayStartUP article %s: %s", url, ex)

            logger.info("Ingested %d Bavarian startups from BayStartUP competitions", len(candidates))

        except Exception as e:
            logger.error("Failed fetching BayStartUP winners: %s", e, exc_info=True)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]
