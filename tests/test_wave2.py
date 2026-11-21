"""
Comprehensive Unit Tests for Wave 2:
1. PLATFORM_DOMAINS isolation (fixing Hugging Face domain collision bug)
2. 5 Deterministic Kill-Gates (Zero-LLM cost)
3. Impressum & commercial intent parsing
4. Transparent 0-100 multi-pillar scoring formula & weighting
5. ML feature contract schema export & ISO-3 conversion
"""

import csv
import json
import tempfile
from pathlib import Path
import pytest

from backend.config import PLATFORM_DOMAINS, BIG_TECH_BLOCKLIST, settings
from backend.db.repository import DatabaseRepository, normalize_domain, slugify
from backend.scoring.kill_gates import KillGateEvaluator, KillGateResult
from backend.enrichment.impressum import ImpressumScraper, ImpressumResult
from backend.enrichment.openai_enricher import EnrichedProfile, LPSynergyMatch, EnrichedFounder
from backend.scoring.scorer import LeadScorer, ScoreBreakdown
from backend.enrichment.github_verifier import GitHubRepoMetrics
from backend.export.ml_exporter import export_ml_contract, ISO2_TO_ISO3
from backend.api.main import app
from starlette.testclient import TestClient


# =============================================================================
# 1. Platform Domains Isolation Tests (Bug 1 Fix)
# =============================================================================
def test_platform_domains_isolation():
    assert "huggingface.co" in PLATFORM_DOMAINS
    assert "github.com" in PLATFORM_DOMAINS
    assert "lu.ma" in PLATFORM_DOMAINS
    assert "linkedin.com" in PLATFORM_DOMAINS

    # Custom domain should normalize properly
    assert normalize_domain("https://syntheta.ai/about") == "syntheta.ai"
    assert normalize_domain("http://www.aixopt.ai") == "aixopt.ai"

    # Platform domains should be detected and isolated to None to prevent collisions
    assert normalize_domain("https://huggingface.co/pyannote") is None
    assert normalize_domain("https://github.com/vllm-project") is None
    assert normalize_domain("https://lu.ma/berlin-ai") is None


# =============================================================================
# 2. Deterministic Kill-Gates Tests
# =============================================================================
@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_kill_gates.db"
        repo = DatabaseRepository(db_path)
        # Seed funding blocklist with known scaleup
        with repo.get_connection() as conn:
            conn.execute(
                "INSERT INTO funding_blocklist (normalized_name, normalized_domain, source, round_info, source_url) "
                "VALUES ('tacto', 'tacto.ai', 'curated_scaleup', 'Series A ($50M+)', 'https://tacto.ai')"
            )
            conn.commit()
        yield repo


def test_gate1_rejects_hobby_and_communities(temp_repo):
    evaluator = KillGateEvaluator(temp_repo)

    # Community event host
    cand_community = {
        "entity_id": "cand_1",
        "canonical_name": "Women in Tech Berlin",
        "description": "Monthly networking and community meetup for female engineers",
        "raw_tags": ["Community", "Meetup"]
    }
    res = evaluator.evaluate(cand_community)
    assert res.gate_passed is False
    assert res.gate1_passed is False
    assert any("Hobby/Community" in r for r in res.kill_reasons)

    # Student homework tutorial
    cand_tutorial = {
        "entity_id": "cand_2",
        "canonical_name": "awesome-deep-learning",
        "description": "A curated list of homework and tutorials for university class",
        "raw_tags": ["Tutorial"]
    }
    res2 = evaluator.evaluate(cand_tutorial)
    assert res2.gate_passed is False
    assert res2.gate1_passed is False


def test_gate2_rejects_it_consultancies_without_ip(temp_repo):
    evaluator = KillGateEvaluator(temp_repo)

    # Pure IT consultancy
    cand_agency = {
        "entity_id": "cand_3",
        "canonical_name": "Bavaria IT Consulting",
        "description": "Unternehmensberatung und IT-Beratung für kleine Unternehmen",
        "raw_tags": ["Consulting"]
    }
    res = evaluator.evaluate(cand_agency)
    assert res.gate_passed is False
    assert res.gate2_passed is False
    assert any("Dev Agency" in r for r in res.kill_reasons)

    # Tech startup with consulting keywords but strong product/IP
    cand_product = {
        "entity_id": "cand_4",
        "canonical_name": "Optima AI",
        "description": "Deeptech SaaS platform and proprietary machine learning algorithms for supply chains",
        "raw_tags": ["DeepTech", "SaaS"]
    }
    res2 = evaluator.evaluate(cand_product)
    assert res2.gate2_passed is True


def test_gate3_rejects_corporate_and_old_companies(temp_repo):
    evaluator = KillGateEvaluator(temp_repo)

    # Legacy corporate
    cand_corp = {
        "entity_id": "cand_5",
        "canonical_name": "Dow Chemical Europe",
        "description": "Global chemicals supplier",
        "raw_tags": ["Chemicals"]
    }
    res = evaluator.evaluate(cand_corp)
    assert res.gate_passed is False
    assert res.gate3_passed is False
    assert any("Legacy Corporate" in r for r in res.kill_reasons)

    # Company older than 3 years (incorporated 2018)
    cand_old = {
        "entity_id": "cand_6",
        "canonical_name": "OldTech Solutions",
        "description": "Software development",
        "incorporation_date": "2018-05-12",
        "raw_tags": ["Software"]
    }
    res2 = evaluator.evaluate(cand_old)
    assert res2.gate_passed is False
    assert res2.gate3_passed is False
    assert any("Age Limit" in r for r in res2.kill_reasons)


def test_gate4_rejects_fake_star_anomalies(temp_repo):
    evaluator = KillGateEvaluator(temp_repo)

    # Suspicious repo: 500 stars but 2 forks (fork_to_star_ratio = 0.004 < 0.03) without academic anchor
    cand_fake = {
        "entity_id": "cand_7",
        "canonical_name": "CryptoBot Hub",
        "description": "Automated trading",
        "raw_tags": ["Crypto"]
    }
    res = evaluator.evaluate(cand_fake, github_stars=500, github_fork_ratio=0.004)
    assert res.gate_passed is False
    assert res.gate4_passed is False
    assert any("Fake-Star Anomaly" in r for r in res.kill_reasons)

    # Valid academic repo with university anchor
    cand_academic = {
        "entity_id": "cand_8",
        "canonical_name": "Aachen Robovision",
        "description": "Robotics perception model",
        "raw_tags": ["RWTH Aachen", "DeepTech"]
    }
    res2 = evaluator.evaluate(cand_academic, github_stars=500, github_fork_ratio=0.01)
    assert res2.gate4_passed is True


def test_gate5_rejects_already_funded_scaleups(temp_repo):
    evaluator = KillGateEvaluator(temp_repo)

    # Tacto (Series A $50M+) is in funding blocklist
    cand_tacto = {
        "entity_id": "cand_9",
        "canonical_name": "Tacto",
        "normalized_domain": "tacto.ai",
        "description": "Procurement for Mittelstand",
        "raw_tags": ["Software"]
    }
    res = evaluator.evaluate(cand_tacto)
    assert res.gate_passed is False
    assert res.gate5_passed is False
    assert any("Already Funded" in r for r in res.kill_reasons)

    # Explicit Series B mentioned in description
    cand_scaleup = {
        "entity_id": "cand_10",
        "canonical_name": "MegaAI",
        "normalized_domain": "megaai.eu",
        "description": "Recently closed a €40M Series B financing round from top VCs",
        "raw_tags": ["AI"]
    }
    res2 = evaluator.evaluate(cand_scaleup)
    assert res2.gate_passed is False
    assert res2.gate5_passed is False


# =============================================================================
# 3. Impressum & Commercial Intent Parser Tests
# =============================================================================
def test_impressum_parser_extracts_hrb_legal_form_and_directors():
    scraper = ImpressumScraper()
    result = ImpressumResult()

    sample_impressum_text = """
    Angaben gemäß § 5 DDG:
    Syntheta Technologies GmbH
    Campus Boulevard 57
    52074 Aachen
    Deutschland

    Handelsregister: Amtsgericht Aachen, HRB 24891
    Umsatzsteuer-Identifikationsnummer: DE 349182390

    Vertreten durch die Geschäftsführer:
    Dr. Florian Weber und Sarah Althaus
    """

    scraper._parse_impressum_text(sample_impressum_text, result)

    assert result.register_number == "HRB 24891"
    assert result.vat_id == "DE349182390"
    assert result.legal_form == "GmbH"
    assert "Syntheta Technologies GmbH" in result.legal_name
    assert "Florian Weber" in result.founders
    assert "Sarah Althaus" in result.founders


# =============================================================================
# 4. Multi-Pillar Scorer Formula & Geographic Weighting Tests
# =============================================================================
def test_lead_scorer_weighting_and_geo_boost():
    candidate_de = {
        "canonical_name": "QuantSens GmbH",
        "country_code": "DE",
        "legal_name": "QuantSens GmbH",
        "raw_tags": ["RWTH Aachen", "DeepTech", "Sensor"],
        "website_url": "https://quantsens.de",
        "signals": [
            {"source_name": "eth_rwth", "source_url": "https://rwth-aachen.de/spinoffs"},
            {"source_name": "openalex", "source_url": "https://openalex.org/W12345"}
        ]
    }

    profile_strong = EnrichedProfile(
        fd_any_phd=True,
        fd_team_has_technical_founder=True,
        fd_any_top_university=True,
        fd_university_spinoff=True,
        is_pre_crunchbase=True,
        htgf_vertical="industrial_climate_deeptech"
    )

    impressum = ImpressumResult(
        legal_name="QuantSens GmbH",
        register_number="HRB 99881",
        has_pricing_or_demo=True,
        deeptech_keywords_found=["quantum", "sensor"]
    )

    breakdown_de = LeadScorer.calculate_score(candidate_de, profile_strong, impressum=impressum)

    # Check pillar caps and weighting
    assert breakdown_de.team_pedigree <= 32.0
    assert breakdown_de.commercial_intent <= 25.0
    assert breakdown_de.technical_moat <= 18.0
    assert breakdown_de.multi_signal_convergence <= 15.0
    assert breakdown_de.htgf_mandate_fit == 10.0  # DE gets 100% boost (10.0)
    assert breakdown_de.final_score >= 60.0

    # Test Swiss entity gets 8.5 mandate fit
    candidate_ch = dict(candidate_de, country_code="CH")
    breakdown_ch = LeadScorer.calculate_score(candidate_ch, profile_strong, impressum=impressum)
    assert breakdown_ch.htgf_mandate_fit == 8.5

    # Test French entity gets 7.0 mandate fit
    candidate_fr = dict(candidate_de, country_code="FR")
    breakdown_fr = LeadScorer.calculate_score(candidate_fr, profile_strong, impressum=impressum)
    assert breakdown_fr.htgf_mandate_fit == 7.0


# =============================================================================
# 5. ML Feature Contract Exporter Tests
# =============================================================================
def test_export_ml_contract_schema_and_types():
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = Path(tmp_dir) / "live_candidates_for_ml.csv"

        sample_candidates = [
            {
                "entity_id": "uuid-test-12345",
                "canonical_name": "Calyx Robotics",
                "website_url": "https://calyxrobotics.de",
                "country_code": "DE",
                "city": "Aachen",
                "incorporation_date": "2025-03-01",
                "raw_tags": ["Robotics", "DeepTech", "AI"],
                "description": "Autonomous robotic micro-manipulators for chip manufacturing",
                "founders": [
                    {"full_name": "Dr. Jonas Keller", "raw_bio": "CTO and co-founder", "linkedin_url": "https://linkedin.com/in/jkeller"}
                ],
                "deterministic_score": 84.5,
                "enriched_profile": EnrichedProfile(
                    fd_any_phd=True,
                    fd_any_top_university=True,
                    fd_team_has_technical_founder=True,
                    fd_university_spinoff=True,
                    is_pre_crunchbase=True,
                    htgf_vertical="industrial_climate_deeptech",
                    lp_synergy_matches=[
                        LPSynergyMatch(lp_name="Robert Bosch GmbH", reason="Automation"),
                        LPSynergyMatch(lp_name="Schunk Group", reason="Gripping")
                    ],
                    inflection_why_now="Microchip manufacturing nearshoring in EU"
                ),
                "score_breakdown": ScoreBreakdown(
                    team_pedigree=20.0,
                    commercial_intent=22.0,
                    technical_moat=18.0,
                    multi_signal_convergence=15.0,
                    htgf_mandate_fit=10.0,
                    final_score=85.0
                )
            }
        ]

        result_path = export_ml_contract(sample_candidates, output_path=csv_path)
        assert result_path.exists()

        with open(result_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        row = rows[0]

        # Verify ISO-3 conversion and core columns
        assert row["company_id"] == "uuid-test-12345"
        assert row["name"] == "Calyx Robotics"
        assert row["country_code"] == "DEU"
        assert row["is_germany"] == "1"
        assert row["city"] == "Aachen"
        assert row["is_university_city"] == "1"
        assert row["profile2026_is_deeptech"] == "1"
        assert row["profile2026_mentions_ai"] == "1"
        assert row["has_cto_founder"] == "1"
        assert row["fd_any_phd"] == "1"
        assert row["fd_university_spinoff"] == "1"
        assert row["is_pre_crunchbase"] == "1"
        assert row["accelerator"] == ""
        assert row["fd_enriched"] == "1"
        assert float(row["deterministic_score"]) == 84.5
        assert "Robert Bosch GmbH" in row["lp_synergy_matches"]


# =============================================================================
# 6. Zero Fake Data & City Normalization Tests
# =============================================================================
def test_zero_fake_data_and_city_normalization():
    from backend.enrichment.openai_enricher import OpenAIEnricher, HTGF_IV_LPS
    from backend.db.repository import normalize_city

    # 1. Carl Zeiss and Trumpf MUST be excluded
    assert "Carl Zeiss" not in HTGF_IV_LPS
    assert "Trumpf" not in HTGF_IV_LPS

    # 2. Verified LPs MUST be present
    assert "Robert Bosch GmbH" in HTGF_IV_LPS
    assert "Schunk Group" in HTGF_IV_LPS
    assert "ALTANA" in HTGF_IV_LPS
    assert "Jungheinrich" in HTGF_IV_LPS

    # 3. NO hardcoded company intelligence dictionary
    enricher_cls = OpenAIEnricher
    assert not hasattr(enricher_cls, "KNOWN_DEEPTECH_INTELLIGENCE")

    # 4. City normalization per features.md & noise stripping
    assert normalize_city("München") == "Munich"
    assert normalize_city("Zürich") == "Zurich"
    assert normalize_city("Köln") == "Cologne"
    assert normalize_city("Garching bei München") == "Munich"
    assert normalize_city("Wien") == "Vienna"
    assert normalize_city("Berlin") == "Berlin"
    assert normalize_city("Aachen Eintragung") == "Aachen"
    assert normalize_city("Grünwald Handelsregister") == "Munich"
    assert normalize_city("Herford Germany") == "Herford"
    assert normalize_city("Kanzlei Berger") is None


# =============================================================================
# 7. CostTracker Circuit-Breaker & URL Cleaning Tests
# =============================================================================
def test_cost_tracker_circuit_breaker():
    from backend.enrichment.cost_tracker import CostTracker

    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger = Path(tmp_dir) / "test_ledger.jsonl"
        tracker = CostTracker(max_budget_usd=0.10, cache_only=False, ledger_path=ledger)

        # Initial checks
        assert tracker.can_execute_live_call(expected_cost=0.03) is True

        # Record a call with 1 web search + 5000 tokens
        cost = tracker.record_live_call(
            entity_id="test_id_1",
            canonical_name="TestStartup",
            input_tokens=4000,
            output_tokens=1000,
            web_search_calls=1
        )
        assert cost >= 0.025  # At least web search call cost
        assert tracker.live_llm_calls == 1

        # Test cache-only mode blocks execution
        tracker_cache_only = CostTracker(cache_only=True, ledger_path=ledger)
        assert tracker_cache_only.can_execute_live_call() is False

        # Test budget cap blocks execution when exceeded
        tracker_capped = CostTracker(max_budget_usd=0.05, ledger_path=ledger)
        tracker_capped.record_live_call("id2", "Startup2", 1000, 1000, web_search_calls=2) # ~$0.05+
        assert tracker_capped.can_execute_live_call(expected_cost=0.03) is False


def test_clean_url_and_linkedin_filter():
    from backend.db.repository import clean_url, is_valid_founder_linkedin

    # clean_url strips utm_source query params
    assert clean_url("https://www.aixopt.ai/?utm_source=openai") == "https://www.aixopt.ai/"
    assert clean_url("https://de.linkedin.com/in/dominik-p%C3%BCllen-54b438319?utm_source=openai") == "https://de.linkedin.com/in/dominik-p%C3%BCllen-54b438319"
    assert clean_url("http://determion.com") == "http://determion.com"

    # Strict LinkedIn founder profile filter
    assert is_valid_founder_linkedin("https://de.linkedin.com/in/dominik-p%C3%BCllen-54b438319") is True
    assert is_valid_founder_linkedin("https://www.flaessig.com/") is False
    assert is_valid_founder_linkedin("https://ch.linkedin.com/company/sherlockrobotics") is False
    assert is_valid_founder_linkedin("https://revonode.ai/team") is False
    assert is_valid_founder_linkedin(None) is False


# =============================================================================
# 8. FastAPI Provenance Endpoints Tests
# =============================================================================
def test_fastapi_endpoints():
    client = TestClient(app)

    # Health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    health_data = res_health.json()
    assert health_data["status"] == "healthy"
    assert "cost_and_cache" in health_data
    assert "cached_leads_count" in health_data["cost_and_cache"]

    # Dashboard endpoint
    res_dash = client.get("/api/dashboard")
    assert res_dash.status_code == 200
    data = res_dash.json()
    assert "meta" in data
    assert "funnel" in data
    assert "ranked_leads" in data
    assert "rejected_audit" in data

    # Leads list with filter
    res_leads = client.get("/api/leads?country=DE")
    assert res_leads.status_code == 200
    leads = res_leads.json().get("leads", [])
    assert len(leads) > 0

    top_id = leads[0]["entity_id"]

    # Lead memo endpoint
    res_memo = client.get(f"/api/leads/{top_id}")
    assert res_memo.status_code == 200
    memo = res_memo.json()
    assert memo["entity_id"] == top_id
    assert "investment_thesis" in memo
    assert "team_dossier" in memo
    assert "lp_synergy_analysis" in memo

    # N8N dispatch endpoint
    auth_headers = {}
    if settings.FOUNDERHUNT_WRITE_TOKEN:
        auth_headers["Authorization"] = f"Bearer {settings.FOUNDERHUNT_WRITE_TOKEN}"
    res_n8n = client.post(f"/api/dispatch/n8n/{top_id}", headers=auth_headers)
    assert res_n8n.status_code == 200
    assert res_n8n.json()["status"] in ["simulated_dispatch", "dispatched"]

    # Rejected noise audit
    res_rej = client.get("/api/rejected")
    assert res_rej.status_code == 200
    assert "rejected_entities" in res_rej.json()

    # Docs endpoint
    res_docs = client.get("/docs")
    assert res_docs.status_code == 200


def test_github_founder_verification_endpoint():
    client = TestClient(app)

    # 1. Non-existent user returns 404
    res_404 = client.get("/api/founders/github/non_existent_founder_xyz_99999")
    assert res_404.status_code == 404

    # 2. Mocked successful verification returns FounderGitHubProfile structure
    from unittest.mock import AsyncMock, patch
    from backend.enrichment.github_verifier import FounderGitHubProfile

    mock_profile = FounderGitHubProfile(
        username="testfounder",
        profile_url="https://github.com/testfounder",
        name="Dr. Tech Founder",
        bio="AI & Robotics researcher",
        company="RoboTech Spin-off",
        location="Munich, Germany",
        blog="https://testfounder.dev",
        public_repos=12,
        followers=450,
        total_stars=1250,
        total_forks=180,
        top_languages=["Python", "C++", "Rust"],
        top_repos=[
            {"name": "deep-robotics", "url": "https://github.com/testfounder/deep-robotics", "stars": 900, "forks": 120, "language": "Python", "pushed_at": "2026-02-15"}
        ],
        social_accounts=[{"provider": "twitter", "url": "https://x.com/testfounder"}]
    )

    with patch("backend.enrichment.github_verifier.GitHubVerifier.verify_founder", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = mock_profile
        res = client.get("/api/founders/github/testfounder")
        assert res.status_code == 200
        data = res.json()
        assert data["username"] == "testfounder"
        assert data["total_stars"] == 1250
        assert "Python" in data["top_languages"]
        assert len(data["top_repos"]) == 1
        assert data["top_repos"][0]["stars"] == 900


def test_optional_ml_score_blend_integration():
    from backend.config import settings
    from backend.api.main import load_frontend_feed, load_ml_predictions

    pred_file = settings.DATA_DIR / "ml_predictions.csv"
    original_content = pred_file.read_bytes() if pred_file.exists() else None
    try:
        # Create temporary ML predictions CSV
        feed = load_frontend_feed()
        leads = feed.get("ranked_leads", [])
        if leads:
            target_id = leads[0]["entity_id"]
            pred_file.write_text(f"company_id,ml_score\n{target_id},92.0\n", encoding="utf-8")

            # Reload feed
            blended_feed = load_frontend_feed()
            matched = next(l for l in blended_feed["ranked_leads"] if l["entity_id"] == target_id)
            assert matched["ml_score"] == 92.0
            det = matched.get("score_breakdown", {}).get("final_score") or matched.get("deterministic_score", 0.0)
            expected_blended = round(0.5 * det + 0.5 * 92.0, 1)
            assert matched["blended_score"] == expected_blended

    finally:
        if original_content is not None:
            pred_file.write_bytes(original_content)
        elif pred_file.exists():
            pred_file.unlink()




def test_founder_flags_follow_founder_records():
    from backend.enrichment.openai_enricher import EnrichedFounder, EnrichedProfile, normalize_profile

    cached = EnrichedProfile(
        founders_enriched=[
            EnrichedFounder(full_name="A", role="CEO", prior_companies=["Accenture", "EY", "CEMEX"], is_ex_bigtech=True),
            EnrichedFounder(full_name="B", role="Co-Founder", prior_companies=["Calspak"]),
        ],
        fd_any_bigtech=True, fd_team_has_technical_founder=True, htgf_vertical="Industrial Tech",
    )
    profile = normalize_profile(cached)
    assert not profile.fd_any_bigtech and not profile.founders_enriched[0].is_ex_bigtech
    assert not profile.fd_team_has_technical_founder  # no founder holds a technical role
    assert profile.htgf_vertical == "industrial_climate_deeptech"

    with_cto = EnrichedProfile(founders_enriched=[
        EnrichedFounder(full_name="C", role="Co-Founder & CTO", prior_companies=["Google DeepMind"])])
    profile = normalize_profile(with_cto)
    assert profile.fd_team_has_technical_founder and profile.fd_any_bigtech

    # A fresh profile: the model's team-level flag counts only with a founder record behind it.
    bare = normalize_profile(EnrichedProfile(founders_enriched=[EnrichedFounder(full_name="D", role="CEO")]),
                             model_says_technical=True)
    assert not bare.fd_team_has_technical_founder
    for backing in ({"has_phd": True}, {"is_technical": True}):
        fresh = normalize_profile(
            EnrichedProfile(founders_enriched=[EnrichedFounder(full_name="D", role="CEO", **backing)]),
            model_says_technical=True)
        assert fresh.fd_team_has_technical_founder
    assert normalize_profile(EnrichedProfile(htgf_vertical="something else")).htgf_vertical == ""


def test_kill_rules_need_a_real_gate():
    from backend.enrichment.openai_enricher import age_kill_reason, funding_kill_reason

    assert age_kill_reason(2021, False, None).startswith("Gate 3")
    assert age_kill_reason(2025, True, "Founded in 2025") is None          # a known recent year wins
    assert age_kill_reason(None, True, "Founded in 2025") is None          # reason names no old year
    assert age_kill_reason(None, True, "Incorporated in 2019").startswith("Gate 3")
    assert age_kill_reason(None, False, None) is None

    assert funding_kill_reason(True, "CHF 150,000 from Venture Kick") is None
    assert funding_kill_reason(True, "EXIST grant and an ERC Proof-of-Concept award") is None
    assert funding_kill_reason(True, "Raised a EUR 4M seed round led by Earlybird").startswith("Gate 5")
    assert funding_kill_reason(True, "Venture Kick prize, then a Series A from Lakestar").startswith("Gate 5")
    assert funding_kill_reason(False, "Series A") is None


def test_empty_research_is_retried_and_never_cached(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from backend.enrichment.cost_tracker import CostTracker
    from backend.enrichment.openai_enricher import EnrichedFounder, OpenAIEnricher, _StructuredWebSearchOutput

    def fake_client(results):
        calls = {"n": 0}

        async def responses_create(**_):
            return SimpleNamespace(output=[], output_text="findings", usage=None)

        async def parse(**_):
            calls["n"] += 1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=results[calls["n"] - 1]))], usage=None)

        client = SimpleNamespace(
            responses=SimpleNamespace(create=responses_create),
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))),
        )
        return client, calls

    empty = _StructuredWebSearchOutput(canonical_name="Hydken")
    good = _StructuredWebSearchOutput(
        canonical_name="Hydken", description="Admixtures for low-carbon concrete.", htgf_vertical="industrial_climate_deeptech",
        raised_institutional_round=True, funding_summary="Venture Kick prize",
        founders=[EnrichedFounder(full_name="Dr. O", role="Co-Founder", has_phd=True, prior_companies=["Accenture"], is_ex_bigtech=True)],
        fd_team_has_technical_founder=True,
    )
    candidate = {"entity_id": "hydken", "canonical_name": "Hydken", "signals": [], "founders": []}

    def enricher(results, name):
        e = OpenAIEnricher(cache_dir=tmp_path / name, api_key="test", cost_tracker=CostTracker(ledger_path=tmp_path / f"{name}.jsonl")
                           if "ledger_path" in CostTracker.__init__.__code__.co_varnames else CostTracker())
        e.client, calls = fake_client(results)
        return e, calls

    # blank first, good second -> one retry, good profile cached
    e, calls = enricher([empty, good], "a")
    profile = asyncio.run(e.enrich_async(candidate))
    assert calls["n"] == 2 and profile.fd_enriched == 1 and len(profile.founders_enriched) == 1
    assert not profile.is_post_enrichment_kill          # a prize is not a funding round
    assert not profile.fd_any_bigtech                    # Accenture is not Big Tech
    assert profile.fd_team_has_technical_founder        # model flag backed by a founder with a PhD
    assert (tmp_path / "a" / "hydken.json").exists()

    # blank twice -> unenriched, nothing cached, so the next run tries again
    e, calls = enricher([empty, empty], "b")
    profile = asyncio.run(e.enrich_async(candidate))
    assert calls["n"] == 2 and profile.fd_enriched == 0
    assert not (tmp_path / "b" / "hydken.json").exists()

    # facts but no founders, twice -> facts are used for this run, the scraped founders stay, nothing is cached
    no_people = _StructuredWebSearchOutput(canonical_name="Hydken", description="Admixtures for low-carbon concrete.",
                                           htgf_vertical="industrial_climate_deeptech")
    scraped = dict(candidate, founders=[{"full_name": "Olga M", "link_method": "impressum"}])
    e, calls = enricher([no_people, no_people], "c")
    profile = asyncio.run(e.enrich_async(scraped))
    assert calls["n"] == 2 and profile.fd_enriched == 1 and profile.description
    assert [f.full_name for f in profile.founders_enriched] == ["Olga M"]
    assert not (tmp_path / "c" / "hydken.json").exists()

    # facts but no founders, then founders on the retry -> the retry wins and is cached
    e, calls = enricher([no_people, good], "d")
    profile = asyncio.run(e.enrich_async(scraped))
    assert calls["n"] == 2 and [f.full_name for f in profile.founders_enriched] == ["Dr. O"]
    assert (tmp_path / "d" / "hydken.json").exists()

    # founders on the first try -> no second call
    e, calls = enricher([good, good], "e")
    asyncio.run(e.enrich_async(scraped))
    assert calls["n"] == 1


def test_clean_github_handle():
    from backend.enrichment.openai_enricher import clean_github_handle

    assert clean_github_handle("https://github.com/torvalds") == "torvalds"
    assert clean_github_handle("@octocat") == "octocat"
    assert clean_github_handle("https://github.com/john-doe_123") is None  # invalid chars for github username
    assert clean_github_handle("valid-user-123") == "valid-user-123"
    assert clean_github_handle("-invalidstart") is None
    assert clean_github_handle(None) is None


def test_gate3_domain_blocklist(tmp_path):
    from backend.db.repository import DatabaseRepository
    from backend.scoring.kill_gates import KillGateEvaluator

    repo = DatabaseRepository(tmp_path / "test.db")
    evaluator = KillGateEvaluator(repo=repo)

    # Candidate with a blocked domain (e.g. ltx.io)
    cand_blocked = {
        "entity_id": "ltx-test",
        "canonical_name": "LTX",
        "normalized_domain": "ltx.io",
        "website_url": "https://ltx.io",
        "description": "Video generation studio"
    }
    res = evaluator.evaluate(cand_blocked)
    assert not res.gate3_passed
    assert not res.gate_passed
    assert any("Gate 3" in r and "ltx" in r.lower() for r in res.kill_reasons)


def test_merge_offline_founder_profiles(tmp_path):
    import json
    from backend.db.repository import DatabaseRepository
    from backend.enrichment.import_founder_links import merge_offline_founder_profiles

    db_path = tmp_path / "test.db"
    repo = DatabaseRepository(db_path)
    entity_id = repo.upsert_entity(canonical_name="Acme AI", normalized_domain="acme.ai")
    f_id = repo.upsert_founder(full_name="Alice Smith")
    repo.link_entity_founder(entity_id=entity_id, founder_id=f_id, link_method="impressum")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / f"{entity_id}.json"
    cache_file.write_text(json.dumps({
        "canonical_name": "Acme AI",
        "founders_enriched": [
            {"full_name": "Alice Smith", "role": "CEO"}
        ]
    }), encoding="utf-8")

    enrich_file = tmp_path / "profiles.json"
    enrich_file.write_text(json.dumps({
        entity_id: {
            "founders": [
                {
                    "full_name": "Alice Smith",
                    "github_handle": "alice-dev",
                    "linkedin_url": "https://linkedin.com/in/alice-smith"
                }
            ]
        }
    }), encoding="utf-8")

    stats = merge_offline_founder_profiles(enrich_file, cache_dir, db_path)
    assert stats["startups_matched"] == 1
    assert stats["cache_files_updated"] == 1
    assert stats["founders_updated"] >= 1

    # Verify cache
    with open(cache_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["founders_enriched"][0]["github_handle"] == "alice-dev"
    assert data["founders_enriched"][0]["linkedin_url"] == "https://linkedin.com/in/alice-smith"


def test_ml_contract_deeptech_and_ai_flags(tmp_path):
    import csv
    from backend.export.ml_exporter import export_ml_contract
    from backend.enrichment.openai_enricher import EnrichedProfile

    cands = [
        {
            "entity_id": "e1",
            "canonical_name": "QuantumCore",
            "country_code": "DE",
            "city": "Munich",
            "description": "Novel semiconductor spintronics technology.",
            "raw_tags": ["hardware", "deeptech"],
            "enriched_profile": EnrichedProfile(
                canonical_name="QuantumCore",
                htgf_vertical="industrial_climate_deeptech",
                founders_enriched=[]
            )
        },
        {
            "entity_id": "e2",
            "canonical_name": "SimpleStore",
            "country_code": "",
            "city": "",
            "description": "Standard e-commerce Shopify plugin.",
            "raw_tags": ["retail"],
            "enriched_profile": EnrichedProfile(
                canonical_name="SimpleStore",
                htgf_vertical="digital_tech",
                founders_enriched=[]
            )
        }
    ]
    out_csv = tmp_path / "out.csv"
    export_ml_contract(cands, out_csv)

    with open(out_csv, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 2
    # e1: deeptech
    assert reader[0]["profile2026_is_deeptech"] == "1"
    assert reader[0]["is_germany"] == "1"
    # e2: non-deeptech, unknown country
    assert reader[1]["profile2026_is_deeptech"] == "0"
    assert reader[1]["is_germany"] == "0"
    assert reader[1]["country_code"] == ""


def test_sort_candidates_prioritize_cache(tmp_path):
    from backend.run_wave2 import sort_candidates_prioritize_cache

    cache_dir = tmp_path / "llm_cache"
    cache_dir.mkdir()

    # Create dummy cache for entity 'e_cached'
    (cache_dir / "e_cached.json").write_text("{}", encoding="utf-8")

    candidates = [
        {"entity_id": "e_uncached_high_signals", "canonical_name": "Zeta Uncached", "signals": [1, 2, 3]},
        {"entity_id": "e_cached", "canonical_name": "Alpha Cached", "signals": [1]},
        {"entity_id": "e_uncached_low_signals", "canonical_name": "Beta Uncached", "signals": [1]},
    ]

    sorted_cands = sort_candidates_prioritize_cache(candidates, cache_dir=cache_dir)
    assert sorted_cands[0]["entity_id"] == "e_cached"
    assert sorted_cands[1]["entity_id"] == "e_uncached_high_signals"
    assert sorted_cands[2]["entity_id"] == "e_uncached_low_signals"


def test_gate1_rejects_luma_only_and_utility_repos(tmp_path):
    from backend.scoring.kill_gates import KillGateEvaluator
    from backend.db.repository import DatabaseRepository

    repo = DatabaseRepository(db_path=tmp_path / "test.db")
    evaluator = KillGateEvaluator(repo=repo)

    # 1. Luma-only candidate should fail Gate 1
    luma_only = {
        "entity_id": "luma1",
        "canonical_name": "House of AI Hamburg",
        "signals": [{"source_name": "luma_events"}]
    }
    res = evaluator.evaluate(luma_only)
    assert not res.gate1_passed
    assert not res.gate_passed
    assert any("Event Organizer" in r for r in res.kill_reasons)

    # 2. Corroborated candidate (Luma + RWTH) should pass Gate 1
    corroborated = {
        "entity_id": "c1",
        "canonical_name": "RoboSpin",
        "description": "Robotics spin-off with proprietary sensing algorithms.",
        "signals": [{"source_name": "luma_events"}, {"source_name": "eth_rwth"}]
    }
    res_corr = evaluator.evaluate(corroborated)
    assert res_corr.gate1_passed

    # 3. Utility / curated list / MCP repo patterns should fail Gate 1
    for utility_name in ["best-of-robot-simulators", "awesome-deeptech", "slack-mcp-server", "esp-labs-web"]:
        cand = {
            "entity_id": f"u_{utility_name}",
            "canonical_name": utility_name,
            "signals": [{"source_name": "github_eu"}]
        }
        res_u = evaluator.evaluate(cand)
        assert not res_u.gate1_passed
        assert any("Utility/List Repo" in r for r in res_u.kill_reasons)


def test_city_normalization_strips_legal_noise():
    from backend.db.repository import normalize_city

    assert normalize_city("Würzburg Vertreten") == "Würzburg"
    assert normalize_city("Stuttgart Postfach") == "Stuttgart"
    assert normalize_city("Augsburg c/o") == "Augsburg"
    assert normalize_city("52062 Aachen") == "Aachen"
    assert normalize_city("München GmbH") == "Munich"
    assert normalize_city("Berlin UG (haftungsbeschränkt)") == "Berlin"


def test_funding_kill_scaleups_and_grants():
    from backend.enrichment.openai_enricher import funding_kill_reason

    # Spott (€21.5M) -> killed
    spott_summary = "Total funding raised: €21.5 million. Seed Round: Raised €3.2 million in March 2025. Series A Round: Raised €18.3 million in September 2026."
    assert funding_kill_reason(False, spott_summary) is not None

    # Expanse (€4.5M) -> killed
    expanse_summary = "Raised €4.5 million ($5.3 million) in a seed round in September 2026, led by Crane Venture Partners..."
    assert funding_kill_reason(False, expanse_summary) is not None

    # TigerShark Science (€1.3M EXIST) -> not killed
    tigershark_summary = "In 2024, TigerShark Science received €1.3 million in EXIST funding."
    assert funding_kill_reason(False, tigershark_summary) is None

    # FIDENTIS (€1.62M EXIST) -> not killed
    fidentis_summary = "Received €1.62 million in funding through the EXIST Transfer of Research program..."
    assert funding_kill_reason(False, fidentis_summary) is None


