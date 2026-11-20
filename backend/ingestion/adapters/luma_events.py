"""
Adapter for Luma Events (lu.ma / luma.com): Ingests German AI/tech/founder demo-day events.
Extracts host startups, communities, and founders from Berlin and Munich discovery pages.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set
from bs4 import BeautifulSoup

from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain

logger = logging.getLogger(__name__)

LUMA_CITIES = [
    ("Berlin", "https://luma.com/berlin"),
    ("Munich", "https://luma.com/munich"),
]

TECH_KEYWORDS = {
    "ai", "ml", "tech", "deeptech", "startup", "founder", "founders",
    "pitch", "demo", "hackathon", "robotics", "quantum", "software",
    "neuro", "bio", "hardware", "venture", "vc", "pre-seed", "seed",
    "accelerator", "incubator", "agent", "llm"
}


COMMUNITY_REGEX = re.compile(
    r"(?i)\b(community|communities|network|women in tech|club|meetup|association|society|chapter|hub|cohort|events|court|summit|conference)\b"
)


def is_community_or_event_org(name: str) -> bool:
    return bool(COMMUNITY_REGEX.search(name))


def is_tech_startup_event(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    tokens = set(re.findall(r"\b\w+\b", text))
    return bool(tokens & TECH_KEYWORDS)


class LumaEventsAdapter(BaseSignalAdapter):
    """Ingests startup demo-days, AI showcases, and host organizations from Luma."""

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="luma_events")
        self.http_client = http_client

    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        seen_entities: Set[str] = set()

        try:
            if own_client:
                await client.__aenter__()

            for city_name, city_url in LUMA_CITIES:
                if len(candidates) >= limit:
                    break

                try:
                    logger.info("Scraping Luma events for %s from %s...", city_name, city_url)
                    html_text = await client.get_text(city_url, use_cache=True)
                    soup = BeautifulSoup(html_text, "html.parser")

                    # Look for Next.js embedded JSON
                    events_data: List[Dict[str, Any]] = []
                    next_data_script = soup.find("script", id="__NEXT_DATA__")
                    if next_data_script and next_data_script.string:
                        try:
                            parsed_next = json.loads(next_data_script.string)
                            events_data = (
                                parsed_next.get("props", {})
                                .get("pageProps", {})
                                .get("initialData", {})
                                .get("data", {})
                                .get("events", [])
                            )
                        except Exception as parse_err:
                            logger.warning("Failed parsing Luma __NEXT_DATA__ for %s: %s", city_name, parse_err)

                    # Fallback to JSON-LD if __NEXT_DATA__ empty
                    if not events_data:
                        for s in soup.find_all("script", type="application/ld+json"):
                            if s.string and "Event" in s.string:
                                try:
                                    ld_obj = json.loads(s.string)
                                    if isinstance(ld_obj, list):
                                        events_data.extend(ld_obj)
                                    elif isinstance(ld_obj, dict):
                                        events_data.append(ld_obj)
                                except Exception:
                                    pass

                    logger.info("Found %d total events in Luma %s", len(events_data), city_name)

                    for item in events_data:
                        if len(candidates) >= limit:
                            break

                        ev = item.get("event") or (item if item.get("@type") == "Event" else {})
                        cal = item.get("calendar") or {}
                        hosts = item.get("hosts") or []

                        ev_title = ev.get("name") or ev.get("summary") or ""
                        ev_slug = ev.get("url") or ev.get("slug") or ev.get("id") or ""
                        ev_start = ev.get("start_at") or ev.get("startDate")
                        cal_name = cal.get("name") or ""

                        if not is_tech_startup_event(ev_title, cal.get("description", "")):
                            continue

                        # Determine host organization or corporate entity
                        org_name = None
                        org_website = None

                        for h in hosts:
                            h_web = h.get("website")
                            h_name = h.get("name")
                            if h_web and not org_website:
                                org_website = h_web
                            # If host looks like an org (no personal first/last or marked as org)
                            if h_name and ("LLC" in h_name or "GmbH" in h_name or "AI" in h_name or "Lab" in h_name or "Space" in h_name or "Network" in h_name):
                                org_name = h_name

                        if not org_name and cal_name and cal_name not in ("Personal", "Private"):
                            org_name = cal_name

                        if not org_name and hosts:
                            org_name = hosts[0].get("name")

                        if not org_name:
                            continue

                        # Bug 3 Fix: Exclude pure event communities / meetup networks
                        if is_community_or_event_org(org_name):
                            logger.debug("Skipping event community/network '%s'", org_name)
                            continue

                        norm_key = org_name.lower().strip()
                        if norm_key in seen_entities:
                            continue
                        seen_entities.add(norm_key)

                        norm_domain = normalize_domain(org_website)

                        # Individual hosts as founders
                        founders: List[RawFounder] = []
                        for h in hosts:
                            h_name = h.get("name")
                            if h_name and h_name != org_name:
                                founders.append(
                                    RawFounder(
                                        full_name=h_name,
                                        raw_bio=h.get("bio"),
                                        link_method="luma_host",
                                        link_confidence=0.75
                                    )
                                )

                        event_url = f"https://luma.com/{ev_slug}" if ev_slug else city_url
                        signal = RawSignal(
                            source_name=self.name,
                            source_native_id=f"luma:{city_name.lower()}:{ev_slug or org_name.lower()}",
                            signal_date=ev_start[:10] if ev_start else None,
                            raw_metrics={
                                "city": city_name,
                                "event_title": ev_title,
                                "event_start": ev_start,
                                "calendar_name": cal_name,
                            },
                            source_url=event_url
                        )

                        candidate = RawCandidate(
                            canonical_name=org_name,
                            normalized_domain=norm_domain,
                            website_url=org_website,
                            country_code="DE",
                            description=f"Host/Organizer of German tech event '{ev_title}' in {city_name}.",
                            raw_tags=["Tech Event Host", "German Ecosystem", city_name, "Luma"],
                            funding_status="unknown",
                            source_name=self.name,
                            source_native_id=signal.source_native_id,
                            signals=[signal],
                            founders=founders[:3]
                        )
                        candidates.append(candidate)

                except Exception as city_err:
                    logger.warning("Error scraping Luma city %s: %s (continuing gracefully)", city_name, city_err)

        except Exception as e:
            logger.error("LumaEventsAdapter fetch failed: %s", e)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]
