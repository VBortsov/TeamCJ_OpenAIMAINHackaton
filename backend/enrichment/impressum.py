"""
German § 5 DDG Impressum & Commercial Intent Scraper.
Extracts verified legal form, HRB commercial register number, managing directors (Geschäftsführer),
commercial intent indicators (demo, pricing, pilots), and linked GitHub/LinkedIn profiles.
"""

from __future__ import annotations
import logging
import re
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_city

logger = logging.getLogger(__name__)

IMPRESSUM_PATHS = [
    "/impressum", "/imprint", "/legal", "/legal-notice",
    "/rechtliches", "/de/impressum", "/en/imprint", "/about", "/team"
]

DEEPTECH_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "deeptech",
    "robotics", "foundation model", "llm", "quantum", "photonics",
    "biotech", "industrial", "autonomous", "semiconductor", "neural"
}

COMMERCIAL_INTENT_KEYWORDS = {
    "demo", "book a demo", "request demo", "pricing", "contact sales",
    "careers", "jobs", "pilot", "enterprise", "solutions", "quote",
    "kostenlose analyse", "kontakt", "preise"
}


class ImpressumResult(BaseModel):
    legal_name: Optional[str] = None
    legal_form: Optional[str] = None
    register_number: Optional[str] = None
    city: Optional[str] = None
    vat_id: Optional[str] = None
    founders: List[str] = Field(default_factory=list)
    has_pricing_or_demo: bool = False
    has_careers: bool = False
    commercial_intent_score: float = 0.0
    deeptech_keywords_found: List[str] = Field(default_factory=list)
    github_org: Optional[str] = None
    linkedin_url: Optional[str] = None
    founder_linkedins: List[str] = Field(default_factory=list)
    founder_githubs: List[str] = Field(default_factory=list)


class ImpressumScraper:
    """Asynchronous crawler for German § 5 DDG corporate disclosures & homepage intent."""

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        self.http_client = http_client

    async def scrape(self, domain_or_url: str) -> ImpressumResult:
        result = ImpressumResult()
        if not domain_or_url:
            return result

        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        # Build base URL
        if not domain_or_url.startswith("http"):
            base_url = f"https://{domain_or_url}"
        else:
            base_url = domain_or_url

        try:
            if own_client:
                await client.__aenter__()

            # 1. Fetch homepage first
            homepage_text = ""
            homepage_soup = None
            try:
                homepage_res = await client.get(base_url, use_cache=True)
                if homepage_res.status_code < 400:
                    homepage_text = homepage_res.text
                    homepage_soup = BeautifulSoup(homepage_text, "html.parser")
            except Exception as ex:
                logger.debug("Homepage fetch failed for %s: %s", base_url, ex)

            # Analyze homepage for commercial intent & links
            legal_page_urls: List[str] = []
            if homepage_soup:
                self._extract_homepage_markers(homepage_soup, base_url, result)
                # Check for footer legal links
                for a in homepage_soup.find_all("a"):
                    href = a.get("href", "")
                    link_text = a.get_text(strip=True).lower()
                    if any(k in link_text or k in href.lower() for k in ["impressum", "imprint", "legal", "rechtliches"]):
                        full_legal = urljoin(base_url, href)
                        if full_legal not in legal_page_urls:
                            legal_page_urls.append(full_legal)

            # Add standard paths if not found on homepage
            for path in IMPRESSUM_PATHS:
                p_url = urljoin(base_url, path)
                if p_url not in legal_page_urls:
                    legal_page_urls.append(p_url)

            # 2. Probe candidate legal/impressum & team pages
            for leg_url in legal_page_urls[:6]:
                try:
                    res = await client.get(leg_url, use_cache=True)
                    if res.status_code == 200 and len(res.text) > 200:
                        leg_soup = BeautifulSoup(res.text, "html.parser")
                        self._extract_founder_social_links(leg_soup, result)
                        text = leg_soup.get_text(separator=" ", strip=True)
                        # Check if page actually looks like legal notice / impressum
                        if any(k in text.lower() for k in ["impressum", "angaben gemäß", "vertreten durch", "handelsregister", "registered office"]):
                            self._parse_impressum_text(text, result)
                            if result.register_number or result.founders:
                                break
                except Exception:
                    continue

        except Exception as e:
            logger.debug("Impressum scraper error on %s: %s", domain_or_url, e)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return result

    def _extract_homepage_markers(self, soup: BeautifulSoup, base_url: str, result: ImpressumResult) -> None:
        text = soup.get_text(separator=" ", strip=True).lower()

        # Keywords
        matched_dt = [kw for kw in DEEPTECH_KEYWORDS if kw in text]
        result.deeptech_keywords_found = list(set(matched_dt))

        has_demo = any(k in text for k in ["demo", "book a demo", "request demo", "kostenlose analyse"])
        has_pricing = any(k in text for k in ["pricing", "preise", "subscription", "enterprise plan"])
        has_careers = any(k in text for k in ["careers", "jobs", "karriere", "join our team", "we are hiring"])

        result.has_pricing_or_demo = has_demo or has_pricing
        result.has_careers = has_careers

        # Compute intent score
        score = 0.2  # baseline for active domain
        if result.has_pricing_or_demo:
            score += 0.4
        if result.has_careers:
            score += 0.2
        if len(result.deeptech_keywords_found) >= 2:
            score += 0.2
        result.commercial_intent_score = min(score, 1.0)

        # External profiles & personal founder links
        self._extract_founder_social_links(soup, result)
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "github.com/" in href and not result.github_org:
                parsed = urlparse(href)
                parts = [p for p in parsed.path.split("/") if p]
                if parts and parts[0] not in ("features", "pricing", "marketplace", "login", "signup", "about"):
                    result.github_org = parts[0]
            if "linkedin.com/company/" in href and not result.linkedin_url:
                result.linkedin_url = href.split("?")[0]

    def _extract_founder_social_links(self, soup: BeautifulSoup, result: ImpressumResult) -> None:
        """Extract personal linkedin.com/in/... and github.com/<user> profile links from team/about pages."""
        for a in soup.find_all("a"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            if "linkedin.com/in/" in href:
                clean_li = href.split("?")[0].rstrip("/")
                if clean_li not in result.founder_linkedins:
                    result.founder_linkedins.append(clean_li)
            elif "github.com/" in href:
                parsed = urlparse(href)
                parts = [p for p in parsed.path.split("/") if p]
                if parts and len(parts) == 1:
                    gh_user = parts[0].strip().lstrip("@")
                    if gh_user.lower() not in (
                        "features", "pricing", "marketplace", "login", "signup", "about",
                        "blog", "contact", "security", "topics", "collections", "events", "trending"
                    ):
                        if gh_user not in result.founder_githubs:
                            result.founder_githubs.append(gh_user)

    def _parse_impressum_text(self, text: str, result: ImpressumResult) -> None:
        # 1. Commercial Register Number (HRB / HRA / CH)
        m_hrb = re.search(r"\b(HRB\s*\d+|HRA\s*\d+|CHE-[\d\.]+)\b", text, re.IGNORECASE)
        if m_hrb and not result.register_number:
            result.register_number = m_hrb.group(1).strip()

        # 2. VAT ID (USt-IdNr)
        m_vat = re.search(r"\b(DE\s*\d{9}|CHE-[\d\.]+\s*(?:MWST|TVA)?)\b", text, re.IGNORECASE)
        if m_vat and not result.vat_id:
            result.vat_id = m_vat.group(1).replace(" ", "")

        # 3. Legal Name & Form
        forms = ["GmbH & Co. KG", "GmbH i.G.", "GmbH", "AG", "UG (haftungsbeschränkt)", "UG", "B.V.", "Ltd"]
        for f in forms:
            pat = rf"\b([A-ZÄÖÜ][a-zA-Z0-9ÄÖÜäöüß\s\-&]+?\b{re.escape(f)})\b"
            m_form = re.search(pat, text)
            if m_form and not result.legal_name:
                cand = m_form.group(1).strip()
                if len(cand) < 40 and not any(w in cand.lower() for w in ("angaben", "kontakt", "adresse", "seite")):
                    result.legal_name = cand
                    result.legal_form = f
                    break

        # 4. City (German Postal code + City pattern: e.g. 52070 Aachen, 80333 München, 10115 Berlin)
        m_city = re.search(r"\b\d{5}[ \t]+([A-ZÄÖÜ][a-zäöüß]+(?:[- ][A-ZÄÖÜ][a-zäöüß]+)?)\b", text)
        if m_city and not result.city:
            result.city = normalize_city(m_city.group(1).strip())

        # 5. Managing Directors (Geschäftsführer / Vertreten durch)
        gf_patterns = [
            r"(?i)(?:vertreten durch(?:\s+(?:die|den|das))?(?:\s+(?:geschäftsführer(?:in)?|vorstand|leitung|inhaber))?|geschäftsführer(?:in)?|geschäftsführung|managing directors?|vorstand)\s*[:\-–]?\s*([A-Za-zÄÖÜäöüß\.\s,&–\-]+?)(?=(?:\n|\r|\bhandelsregister\b|\bregistergericht\b|\bamtsgericht\b|\bumsatzsteuer\b|\bust-id\b|\bkontakt\b|\btelefon\b|\be-mail\b|\bemail\b|\bquelle\b|\bhaftung\b|\bhrb\b|\bhra\b|$))",
        ]
        for pat in gf_patterns:
            m_names = re.search(pat, text)
            if m_names:
                raw_names_block = m_names.group(1).strip()
                # Split multiple names by comma, '&', 'und', or newline
                split_names = re.split(r"\s*(?:,|&|\bund\b|\n)\s*", raw_names_block)
                for nm in split_names:
                    nm = re.sub(r"^(?:Herr|Frau|Dr\.|Prof\.|Dipl\.-[A-Za-z]+)\s*", "", nm).strip()
                    tokens = nm.split()
                    if 2 <= len(tokens) <= 3 and nm not in result.founders:
                        if not any(stop in nm.lower() for stop in ("kontakt", "telefon", "email", "straße", "register", "amtsgericht", "geschäftsführer", "angaben")):
                            result.founders.append(nm)
                if result.founders:
                    break
