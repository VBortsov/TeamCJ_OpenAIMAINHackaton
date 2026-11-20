"""
Unit tests for adapter parsers and filtering rules.
"""

from backend.ingestion.adapters.eth_rwth import clean_legal_name
from backend.ingestion.adapters.openalex import is_corporate_or_big_tech, clean_company_name
from backend.ingestion.adapters.hf_hub import is_excluded_hf_org
from backend.ingestion.adapters.luma_events import is_tech_startup_event


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
