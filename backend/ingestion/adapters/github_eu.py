"""
European Open Source DeepTech Signal Adapter (GitHub Search API).
Discovers high-velocity European DeepTech repositories created since 2025-01-01
with verified European maintainers and organic fork-to-star ratios (Gate 4 compliance).
"""

from __future__ import annotations
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from backend.config import settings
from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain, normalize_city, slugify

logger = logging.getLogger(__name__)

GITHUB_SEARCH_REPOS_URL = "https://api.github.com/search/repositories"
GITHUB_USERS_URL = "https://api.github.com/users"

DEEPTECH_TOPICS = [
    "robotics",
    "quantum",
    "llm",
    "autonomous",
    "biotech",
    "deeptech",
    "photonics",
    "industrial",
    "cybersecurity",
    "embedded"
]

# Specific cities first to preserve accurate city attribution
EU_CITY_MAP: Dict[str, Tuple[str, str]] = {
    "munich": ("DE", "Munich"),
    "münchen": ("DE", "Munich"),
    "berlin": ("DE", "Berlin"),
    "aachen": ("DE", "Aachen"),
    "hamburg": ("DE", "Hamburg"),
    "frankfurt": ("DE", "Frankfurt"),
    "cologne": ("DE", "Cologne"),
    "köln": ("DE", "Cologne"),
    "stuttgart": ("DE", "Stuttgart"),
    "dresden": ("DE", "Dresden"),
    "karlsruhe": ("DE", "Karlsruhe"),
    "heidelberg": ("DE", "Heidelberg"),
    "zurich": ("CH", "Zurich"),
    "zürich": ("CH", "Zurich"),
    "lausanne": ("CH", "Lausanne"),
    "geneva": ("CH", "Geneva"),
    "basel": ("CH", "Basel"),
    "vienna": ("AT", "Vienna"),
    "wien": ("AT", "Vienna"),
    "graz": ("AT", "Graz"),
    "paris": ("FR", "Paris"),
    "lyon": ("FR", "Lyon"),
    "toulouse": ("FR", "Toulouse"),
    "amsterdam": ("NL", "Amsterdam"),
    "delft": ("NL", "Delft"),
    "eindhoven": ("NL", "Eindhoven"),
    "london": ("GB", "London"),
    "cambridge": ("GB", "Cambridge"),
    "oxford": ("GB", "Oxford"),
    "stockholm": ("SE", "Stockholm"),
    "copenhagen": ("DK", "Copenhagen"),
}

EU_COUNTRY_MAP: Dict[str, Tuple[str, str]] = {
    "germany": ("DE", "Berlin"),
    "deutschland": ("DE", "Berlin"),
    "switzerland": ("CH", "Zurich"),
    "schweiz": ("CH", "Zurich"),
    "austria": ("AT", "Vienna"),
    "österreich": ("AT", "Vienna"),
    "france": ("FR", "Paris"),
    "netherlands": ("NL", "Amsterdam"),
    "united kingdom": ("GB", "London"),
    "uk": ("GB", "London"),
    "sweden": ("SE", "Stockholm"),
    "denmark": ("DK", "Copenhagen"),
}


def detect_eu_location(location_str: Optional[str], homepage_url: Optional[str], blog_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Determine European country code and city from profile signals."""
    loc = (location_str or "").lower()
    # 1. Match specific cities first
    for name_hint, (cc, city) in EU_CITY_MAP.items():
        if re.search(rf"\b{re.escape(name_hint)}\b", loc):
            return cc, city

    # 2. Match general country names
    for name_hint, (cc, city) in EU_COUNTRY_MAP.items():
        if re.search(rf"\b{re.escape(name_hint)}\b", loc):
            return cc, city

    # 3. Match TLDs
    for u in [homepage_url, blog_url]:
        if u:
            try:
                host = urlparse(u).netloc.lower()
                for cc in ["de", "ch", "at", "fr", "nl", "se", "dk", "uk"]:
                    if host.endswith(f".{cc}"):
                        default_city = EU_COUNTRY_MAP.get(cc, (cc.upper(), "Metropolis"))[1]
                        return cc.upper(), default_city
            except Exception:
                pass

    return None, None


class GitHubEuDeeptechAdapter(BaseSignalAdapter):
    """
    Adapter for European Open Source DeepTech repositories on GitHub.
    Filters for recent (>= 2025) projects with organic adoption (forks/stars >= 0.05).
    """

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="github_eu")
        self.http_client = http_client

    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        token = settings.GITHUB_TOKEN or os.environ.get("GITHUB_TOKEN")
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "FounderHunt-DeepTech/1.0"
        }
        if token:
            headers["Authorization"] = f"token {token}"

        seen_repos: Set[str] = set()

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Searching GitHub for European DeepTech projects (created >= 2025)...")

            for topic in DEEPTECH_TOPICS:
                if len(candidates) >= limit:
                    break

                q = f"created:>2025-01-01 stars:15..2000 {topic}"
                params = {
                    "q": q,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 25
                }

                try:
                    res = await client.get_json(
                        GITHUB_SEARCH_REPOS_URL,
                        params=params,
                        headers=headers,
                        use_cache=True
                    )
                    items = res.get("items", []) if isinstance(res, dict) else []

                    for it in items:
                        if len(candidates) >= limit:
                            break

                        full_name = it.get("full_name", "")
                        if not full_name or full_name in seen_repos:
                            continue
                        seen_repos.add(full_name)

                        stars = it.get("stargazers_count", 0)
                        forks = it.get("forks_count", 0)

                        # Gate 4: StarScout anti-fake-star filter
                        if stars > 0 and (forks / stars < 0.05):
                            continue

                        owner_obj = it.get("owner", {})
                        owner_login = owner_obj.get("login")
                        if not owner_login:
                            continue

                        # Check owner location
                        owner_res = await client.get_json(
                            f"{GITHUB_USERS_URL}/{owner_login}",
                            headers=headers,
                            use_cache=True
                        )
                        u_loc = owner_res.get("location") if isinstance(owner_res, dict) else ""
                        u_blog = owner_res.get("blog") if isinstance(owner_res, dict) else ""
                        homepage = it.get("homepage") or ""

                        cc, city = detect_eu_location(u_loc, homepage, u_blog)
                        if not cc:
                            continue  # Skip non-European or unverified location

                        repo_name = it.get("name", "")
                        desc = it.get("description") or f"Open-source {topic} project created by {owner_login} ({city}, {cc})."
                        html_url = it.get("html_url") or f"https://github.com/{full_name}"
                        created_at = it.get("created_at", "2025-01-01")[:10]
                        lang = it.get("language") or "Code"

                        # Build founder profile for repo owner
                        founders = [
                            RawFounder(
                                full_name=owner_res.get("name") or owner_login,
                                github_handle=owner_login,
                                link_method="github_repo_owner",
                                link_confidence=0.85
                            )
                        ]

                        norm_domain = normalize_domain(homepage) if homepage else None
                        slug = slugify(repo_name)

                        signal = RawSignal(
                            source_name=self.name,
                            source_native_id=f"gh:{full_name}",
                            signal_date=created_at,
                            source_url=html_url,
                            summary=f"Verified European Open Source DeepTech ({city}, {cc}): {full_name} ({stars} stars)",
                            raw_metrics={
                                "repo": full_name,
                                "stars": stars,
                                "forks": forks,
                                "fork_ratio": round(forks / max(1, stars), 3),
                                "language": lang,
                                "owner": owner_login,
                                "topic": topic,
                                "city": city,
                                "country_code": cc
                            }
                        )

                        candidate = RawCandidate(
                            canonical_name=repo_name,
                            normalized_domain=norm_domain,
                            website_url=homepage if (homepage and "http" in homepage) else html_url,
                            country_code=cc,
                            city=city,
                            description=desc,
                            raw_tags=["OpenSource", "GitHub", "DeepTech", topic, lang],
                            funding_status="unknown",
                            source_name=self.name,
                            source_native_id=signal.source_native_id,
                            signals=[signal],
                            founders=founders
                        )
                        candidates.append(candidate)

                except Exception as ex:
                    logger.warning("GitHub query failed for topic %s: %s", topic, ex)

            logger.info("Ingested %d verified European Open Source DeepTech projects", len(candidates))

        except Exception as e:
            logger.error("Failed fetching European GitHub repositories: %s", e, exc_info=True)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]
