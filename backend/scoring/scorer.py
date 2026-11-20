"""
Transparent Multi-Pillar Deterministic Lead Scorer.
Formula:
Score (0-100) = Team Pedigree (32%) + Commercial Intent (25%) +
                Traction & Technical Moat (18%) + Multi-Signal Convergence (15%) +
                HTGF Mandate Fit (10%, boosting DE=1.0, CH/AT=0.85, EU=0.70)
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.enrichment.openai_enricher import EnrichedProfile
from backend.enrichment.impressum import ImpressumResult
from backend.enrichment.github_verifier import GitHubRepoMetrics

logger = logging.getLogger(__name__)


class ScoreBreakdown(BaseModel):
    team_pedigree: float = Field(ge=0.0, le=32.0)
    commercial_intent: float = Field(ge=0.0, le=25.0)
    technical_moat: float = Field(ge=0.0, le=18.0)
    multi_signal_convergence: float = Field(ge=0.0, le=15.0)
    htgf_mandate_fit: float = Field(ge=0.0, le=10.0)
    final_score: float = Field(ge=0.0, le=100.0)
    evidence_urls: List[str] = Field(default_factory=list)


class LeadScorer:
    """Computes transparent, auditable 0-100 scores for early-stage leads."""

    @staticmethod
    def calculate_score(
        candidate: Dict[str, Any],
        profile: EnrichedProfile,
        impressum: Optional[ImpressumResult] = None,
        github: Optional[GitHubRepoMetrics] = None
    ) -> ScoreBreakdown:
        signals = candidate.get("signals", [])
        country = (candidate.get("country_code") or "").upper()
        tags = set(t.lower() for t in candidate.get("raw_tags", []))

        # -------------------------------------------------------------
        # Pillar 1: Team Pedigree (32 max)
        # -------------------------------------------------------------
        team_pts = 0.0
        if profile.fd_any_phd:
            team_pts += 8.0
        if profile.fd_team_has_technical_founder:
            team_pts += 7.0
        if profile.fd_any_bigtech:
            team_pts += 6.0
        if profile.fd_any_top_university:
            team_pts += 5.0
        if profile.fd_any_prior_exit or profile.fd_any_serial_founder:
            team_pts += 4.0
        if profile.fd_any_academic_researcher:
            team_pts += 2.0
        team_pedigree = min(team_pts, 32.0)

        # -------------------------------------------------------------
        # Pillar 2: Commercial Intent (25 max)
        # -------------------------------------------------------------
        intent_pts = 0.0
        if impressum and (impressum.legal_name or impressum.register_number):
            intent_pts += 10.0
        elif candidate.get("legal_name") or candidate.get("legal_form"):
            intent_pts += 7.0

        if impressum and impressum.has_pricing_or_demo:
            intent_pts += 8.0
        elif "pricing" in str(signals) or "demo" in str(signals):
            intent_pts += 4.0

        if impressum and impressum.has_careers:
            intent_pts += 4.0

        if impressum and impressum.deeptech_keywords_found:
            intent_pts += 3.0
        commercial_intent = min(intent_pts, 25.0)

        # -------------------------------------------------------------
        # Pillar 3: Traction & Technical Moat (18 max)
        # -------------------------------------------------------------
        moat_pts = 0.0
        # Academic awards & grant anchors
        if any(t in tags for t in ["rwth aachen", "eth zurich", "spin-off award", "erc proof-of-concept"]):
            moat_pts += 10.0
        elif profile.fd_university_spinoff:
            moat_pts += 7.0

        # Technical traction (GitHub stars / Hugging Face downloads)
        if github and github.stargazer_count > 50 and not github.is_fake_star_suspect:
            moat_pts += 5.0
        elif any(s.get("source_name") == "hf_hub" for s in signals):
            moat_pts += 5.0

        if candidate.get("normalized_domain") or candidate.get("website_url"):
            moat_pts += 3.0
        technical_moat = min(moat_pts, 18.0)

        # -------------------------------------------------------------
        # Pillar 4: Multi-Signal Convergence (15 max)
        # -------------------------------------------------------------
        conv_pts = 0.0
        distinct_sources = len(set(s.get("source_name") for s in signals))
        if distinct_sources >= 2:
            conv_pts += 10.0
        elif len(signals) >= 1:
            conv_pts += 5.0

        if any(s.get("source_name") == "openalex" for s in signals):
            conv_pts += 5.0
        multi_signal_convergence = min(conv_pts, 15.0)

        # -------------------------------------------------------------
        # Pillar 5: HTGF Mandate Fit & Geographic Weight (10 max)
        # -------------------------------------------------------------
        if country == "DE":
            htgf_mandate_fit = 10.0  # 100% boost for German core mandate
        elif country in ("CH", "AT"):
            htgf_mandate_fit = 8.5   # 85% boost for DACH
        elif country in ("NL", "FR", "BE", "GB", "DK", "SE", "IT", "ES"):
            htgf_mandate_fit = 7.0   # 70% boost for European secondary
        else:
            htgf_mandate_fit = 3.0

        # Out-of-mandate penalty
        if profile.htgf_vertical == "out_of_mandate":
            htgf_mandate_fit *= 0.2

        final_score = round(
            team_pedigree + commercial_intent + technical_moat + multi_signal_convergence + htgf_mandate_fit,
            1
        )

        # Collect evidence URLs
        evidence_urls = [s.get("source_url") for s in signals if s.get("source_url")]
        if candidate.get("website_url"):
            evidence_urls.insert(0, candidate["website_url"])

        return ScoreBreakdown(
            team_pedigree=round(team_pedigree, 1),
            commercial_intent=round(commercial_intent, 1),
            technical_moat=round(technical_moat, 1),
            multi_signal_convergence=round(multi_signal_convergence, 1),
            htgf_mandate_fit=round(htgf_mandate_fit, 1),
            final_score=min(max(final_score, 0.0), 100.0),
            evidence_urls=evidence_urls[:4]
        )
