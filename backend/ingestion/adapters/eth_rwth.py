"""
Adapter for ETH Zurich Spin-offs & RWTH Aachen Spin-off Award winners.
Scrapes official university spin-off portals for recent (2025-2026) ventures.
"""

from __future__ import annotations
import html
import logging
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain

logger = logging.getLogger(__name__)

ETH_OVERVIEW_URL = "https://entrepreneurship.ethz.ch/startup-stories/explore-startup-portraits-and-success-stories/uebersicht-eth-spin-offs.html"
ETH_FALLBACK_JSON_URL = "https://entrepreneurship.ethz.ch/startup-stories/explore-startup-portraits-and-success-stories/uebersicht-eth-spin-offs/_jcr_content/par/table.tableComp.json"
RWTH_AWARD_URL = "https://www.rwth-aachen.de/cms/root/wir/profil/auszeichnungen-und-ehrungen/~ndxw/spin-off-award/?lidx=1"


def clean_legal_name(raw_name: str) -> tuple[str, Optional[str], Optional[str]]:
    """
    Extract canonical name, legal name, and legal form.
    e.g. 'Hydken GmbH' -> ('Hydken', 'Hydken GmbH', 'GmbH')
    """
    name = html.unescape(raw_name).strip()
    name = re.sub(r"\s+", " ", name)
    legal_forms = [
        "GmbH & Co. KG", "GmbH", "AG", "UG (haftungsbeschränkt)", "UG",
        "Inc.", "Inc", "Pte Ltd", "Ltd.", "Ltd", "LLC", "SA", "GmbH i.G."
    ]
    legal_name = name
    canonical = name
    detected_form = None

    for form in legal_forms:
        pattern = rf"\b{re.escape(form)}$"
        if re.search(pattern, canonical, re.IGNORECASE):
            canonical = re.sub(pattern, "", canonical, flags=re.IGNORECASE).strip()
            detected_form = form
            break

    # Strip trailing punctuation
    canonical = canonical.strip(",.- ")
    if not canonical:
        canonical = name

    return canonical, legal_name, detected_form


class EthRwthAdapter(BaseSignalAdapter):
    """Scrapes official ETH Zurich and RWTH Aachen spin-off directories."""

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="eth_rwth")
        self.http_client = http_client

    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        try:
            if own_client:
                await client.__aenter__()

            # Pull all 2025-2026 rows up to limit from both elite institutions
            eth_candidates = await self._fetch_eth_spinoffs(client, limit=limit)
            candidates.extend(eth_candidates)
            logger.info("Fetched %d candidates from ETH Zurich", len(eth_candidates))

            rwth_candidates = await self._fetch_rwth_spinoffs(client, limit=limit)
            candidates.extend(rwth_candidates)
            logger.info("Fetched %d candidates from RWTH Aachen", len(rwth_candidates))

        except Exception as e:
            logger.error("Error in EthRwthAdapter.fetch: %s", e, exc_info=True)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]

    async def _fetch_eth_spinoffs(self, client: CachedHttpClient, limit: int) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        try:
            # First, check overview page for table JSON URL
            json_url = ETH_FALLBACK_JSON_URL
            try:
                html_text = await client.get_text(ETH_OVERVIEW_URL, use_cache=True)
                soup = BeautifulSoup(html_text, "html.parser")
                table_div = soup.find(attrs={"data-json-url": True})
                if table_div and table_div.get("data-json-url"):
                    relative_url = table_div["data-json-url"]
                    json_url = urljoin(ETH_OVERVIEW_URL, relative_url)
            except Exception as e:
                logger.warning("Could not parse ETH overview HTML, falling back to direct JSON URL: %s", e)

            # Fetch the data table
            table_data = await client.get_json(json_url, use_cache=True)
            rows = table_data.get("tbody", {}).get("rows", [])
            logger.info("Found %d total rows in ETH Zurich tableComp", len(rows))

            for row in rows:
                if len(candidates) >= limit:
                    break

                cells = row.get("cells", [])
                if len(cells) < 4:
                    continue

                raw_name = cells[0].get("content", "").strip()
                spin_type = cells[1].get("content", "").strip()
                approval_year = cells[2].get("content", "").strip()
                website_raw = cells[3].get("content", "").strip()
                department = cells[4].get("content", "").strip() if len(cells) > 4 else ""

                if not raw_name or raw_name in ("&nbsp;", "-"):
                    continue

                # Filter strictly for 2025 and 2026 cohorts
                if approval_year not in ("2025", "2026"):
                    continue

                website_url = None
                if website_raw and website_raw not in ("&nbsp;", "-", "https://N/A", "http://N/A"):
                    # Extract href if HTML or plain URL
                    if "<a" in website_raw:
                        a_soup = BeautifulSoup(website_raw, "html.parser")
                        a_tag = a_soup.find("a")
                        if a_tag and a_tag.get("href"):
                            website_url = a_tag["href"]
                    else:
                        website_url = website_raw.strip()

                norm_domain = normalize_domain(website_url)
                canonical_name, legal_name, legal_form = clean_legal_name(raw_name)

                signal = RawSignal(
                    source_name=self.name,
                    source_native_id=f"eth:{approval_year}:{canonical_name.lower()}",
                    signal_date=f"{approval_year}-01-01",
                    raw_metrics={
                        "university": "ETH Zurich",
                        "spin_off_type": spin_type,
                        "approval_year": int(approval_year) if approval_year.isdigit() else 2026,
                        "department": department,
                    },
                    source_url=ETH_OVERVIEW_URL
                )

                candidate = RawCandidate(
                    canonical_name=canonical_name,
                    normalized_domain=norm_domain,
                    website_url=website_url,
                    legal_name=legal_name,
                    legal_form=legal_form,
                    country_code="CH",
                    city="Zurich",
                    description=f"{spin_type} from ETH Zurich ({department or 'General'}), approved {approval_year}.",
                    raw_tags=["DeepTech", "Academic Spin-off", "ETH Zurich", spin_type, department or "ETH"],
                    funding_status="unknown",
                    source_name=self.name,
                    source_native_id=signal.source_native_id,
                    signals=[signal]
                )
                candidates.append(candidate)

        except Exception as e:
            logger.error("Failed fetching ETH Zurich spin-offs: %s", e)

        return candidates

    async def _fetch_rwth_spinoffs(self, client: CachedHttpClient, limit: int) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        try:
            html_text = await client.get_text(RWTH_AWARD_URL, use_cache=True)
            soup = BeautifulSoup(html_text, "html.parser")

            award_sections = []
            for h2 in soup.find_all("h2"):
                txt = h2.get_text(strip=True)
                if "Spin-off Award" in txt:
                    year = "2026" if "2026" in txt else ("2025" if "2025" in txt else "2026")
                    table = h2.find_next("table")
                    if table:
                        for a in table.find_all("a"):
                            cname = a.get_text(strip=True)
                            href = urljoin(RWTH_AWARD_URL, a.get("href", ""))
                            if cname and href:
                                award_sections.append({"name": cname, "url": href, "year": year})

            logger.info("Found %d RWTH spin-off award entries", len(award_sections))

            skip_domains = {
                "rwth-aachen.de", "studierendenwerk-aachen.de", "facebook.com",
                "linkedin.com", "xing.com", "instagram.com", "youtube.com",
                "tiktok.com", "threads.com", "bsky.app", "ukaachen.de",
                "jara.org", "wisskomm.social"
            }

            for item in award_sections[:limit]:
                cname = item["name"]
                sub_url = item["url"]
                year = item["year"]

                subpage_desc = None
                company_site = None

                try:
                    sub_html = await client.get_text(sub_url, use_cache=True)
                    sub_soup = BeautifulSoup(sub_html, "html.parser")

                    # Extract external link
                    for a in sub_soup.find_all("a"):
                        href = a.get("href", "")
                        if href.startswith("http"):
                            netloc = urlparse(href).netloc.lower()
                            if not any(sd in netloc for sd in skip_domains):
                                company_site = href
                                break

                    # Extract description paragraphs
                    txt_wrapper = sub_soup.find("div", class_="text-wrapper") or sub_soup
                    paras = [
                        p.get_text(strip=True)
                        for p in txt_wrapper.find_all("p")
                        if len(p.get_text(strip=True)) > 50
                    ]
                    if paras:
                        subpage_desc = " ".join(paras[:2])
                except Exception as ex:
                    logger.warning("Failed fetching RWTH subpage for %s: %s", cname, ex)

                norm_domain = normalize_domain(company_site)
                canonical_name, legal_name, legal_form = clean_legal_name(cname)

                signal = RawSignal(
                    source_name=self.name,
                    source_native_id=f"rwth:{year}:{canonical_name.lower()}",
                    signal_date=f"{year}-01-01",
                    raw_metrics={
                        "university": "RWTH Aachen University",
                        "award": "RWTH Spin-off Award",
                        "year": int(year) if year.isdigit() else 2026,
                    },
                    source_url=sub_url
                )

                candidate = RawCandidate(
                    canonical_name=canonical_name,
                    normalized_domain=norm_domain,
                    website_url=company_site,
                    legal_name=legal_name,
                    legal_form=legal_form,
                    country_code="DE",
                    city="Aachen",
                    description=subpage_desc or f"RWTH Aachen Spin-off Award Winner ({year}).",
                    raw_tags=["DeepTech", "Spin-off", "RWTH Aachen", "Spin-off Award", "Industrial AI"],
                    funding_status="unknown",
                    source_name=self.name,
                    source_native_id=signal.source_native_id,
                    signals=[signal]
                )
                candidates.append(candidate)

        except Exception as e:
            logger.error("Failed fetching RWTH Aachen spin-offs: %s", e)

        return candidates
