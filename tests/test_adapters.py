"""
Unit tests for adapter parsers and filtering rules.
"""

from backend.ingestion.adapters.eth_rwth import clean_legal_name
from backend.ingestion.adapters.openalex import is_corporate_or_big_tech, clean_company_name
from backend.ingestion.adapters.hf_hub import is_excluded_hf_org
from backend.ingestion.adapters.luma_events import is_tech_startup_event
from backend.ingestion.adapters.baystartup import clean_baystartup_company_name, BayStartUPAdapter
from backend.ingestion.adapters.tum_venture_labs import TumVentureLabsAdapter, LAB_DOMAINS
from backend.ingestion.adapters.github_eu import detect_eu_location, GitHubEuDeeptechAdapter


def test_clean_legal_name():
    c, l, f = clean_legal_name("Hydken GmbH")
    assert c == "Hydken"
    assert l == "Hydken GmbH"
    assert f == "GmbH"

    c2, l2, f2 = clean_legal_name("Ascento AG")
    assert c2 == "Ascento"
    assert f2 == "AG"

    c3, l3, f3 = clean_legal_name("Tethys Robotics")
    assert c3 == "Tethys Robotics"
    assert f3 is None


def test_openalex_filtering():
    assert is_corporate_or_big_tech("Google LLC") is True
    assert is_corporate_or_big_tech("BASF SE") is True
    assert is_corporate_or_big_tech("Robert Bosch GmbH") is True
    assert is_corporate_or_big_tech("Novo Nordisk (Denmark)") is True
    assert is_corporate_or_big_tech("Syntheta AI") is False
    assert is_corporate_or_big_tech("Calyx DeepTech Ltd") is False

    assert clean_company_name("ATG Europe (Netherlands)") == "ATG Europe"
    assert clean_company_name("Syntheta AI") == "Syntheta AI"


def test_hf_filtering():
    assert is_excluded_hf_org("google") is True
    assert is_excluded_hf_org("meta-llama") is True
    assert is_excluded_hf_org("apple") is True
    assert is_excluded_hf_org("nvidia") is True
    assert is_excluded_hf_org("Zyphra") is False
    assert is_excluded_hf_org("higurashi-ai") is False


def test_luma_filtering():
    assert is_tech_startup_event("LMU Startup in 2 days", "Founder pitching") is True
    assert is_tech_startup_event("Berlin AI Demo Day", "") is True
    assert is_tech_startup_event("Dinner with Friends", "Lebanese long table") is False


def test_baystartup_cleaning():
    assert clean_baystartup_company_name("senze.ai – KI macht kollektive Stimmungen messbar") == "senze.ai"
    assert clean_baystartup_company_name("Athegus von der Technischen Hochschule Deggendorf") == "Athegus"
    assert clean_baystartup_company_name("Stellaris vom ITC1 Deggendorf") == "Stellaris"
    assert clean_baystartup_company_name("QOODA aus München") == "QOODA"
    assert clean_baystartup_company_name("micro factory 3DSolutions GmbH") == "micro factory 3DSolutions"

    adapter = BayStartUPAdapter()
    assert adapter.name == "baystartup"


def test_tum_venture_labs_adapter():
    adapter = TumVentureLabsAdapter()
    assert adapter.name == "tum_venture_labs"
    assert len(LAB_DOMAINS) >= 10
    domain_slugs = [d[0] for d in LAB_DOMAINS]
    assert "software-ai" in domain_slugs
    assert "quantum" in domain_slugs
    assert "robotics-ai" in domain_slugs


def test_github_eu_detection():
    cc, city = detect_eu_location("Munich, Germany", None, None)
    assert cc == "DE"
    assert city == "Munich"

    cc_ch, city_ch = detect_eu_location("Zurich, Switzerland", None, None)
    assert cc_ch == "CH"
    assert city_ch == "Zurich"

    cc_fr, city_fr = detect_eu_location("Paris, France", None, None)
    assert cc_fr == "FR"
    assert city_fr == "Paris"

    cc_tld, _ = detect_eu_location(None, "https://startup.de", None)
    assert cc_tld == "DE"

    cc_none, _ = detect_eu_location("San Francisco, CA", "https://app.io", None)
    assert cc_none is None

    adapter = GitHubEuDeeptechAdapter()
    assert adapter.name == "github_eu"


def test_platform_domains_distinct_entity_ids():
    from backend.db.repository import generate_entity_id, normalize_domain
    from backend.config import PLATFORM_DOMAINS

    assert "tum-venture-labs.de" in PLATFORM_DOMAINS
    assert "baystartup.de" in PLATFORM_DOMAINS
    assert "legaltechcolab.com" in PLATFORM_DOMAINS

    # Neither portal domain should normalize to an entity domain
    assert normalize_domain("https://www.tum-venture-labs.de/teams/holloid") is None
    assert normalize_domain("https://www.baystartup.de/startupdate/nordbayern-2026") is None

    # Two distinct startups from TUM Venture Labs must NOT share the same entity_id
    id1 = generate_entity_id(
        canonical_name="Holloid",
        normalized_domain=None,
        source_name="tum_venture_labs",
        source_native_id="tum:holloid"
    )
    id2 = generate_entity_id(
        canonical_name="Autonomous Navy",
        normalized_domain=None,
        source_name="tum_venture_labs",
        source_native_id="tum:autonomous-navy"
    )
    assert id1 != id2

