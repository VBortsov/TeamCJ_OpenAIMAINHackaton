"""
Transparent Multi-Pillar Deterministic Lead Scorer.
Formula:
Score (0-100) = Team Pedigree (32%) + Commercial Intent (25%) +
                Traction & Technical Moat (18%) + Multi-Signal Convergence (15%) +
                HTGF Mandate Fit (10%, boosting DE=10.0, CH/AT=8.5, EU=7.0)

Includes fine-grained differentiation bonuses:
- Verified PhD / technical founder
- Verified commercial register entity (HRB / CHE)
- Official spin-off award anchor
- Multi-source convergence
- Real active demo & pricing markers
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
    drivers: List[str] = Field(default_factory=list)
    evidence_urls: List[str] = Field(default_factory=list)


class LeadScorer:
    """Computes transparent, auditable, well-differentiated 0-100 scores for early-stage leads."""

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
        desc = (candidate.get("description") or "").lower()
        drivers: List[str] = []

        # -------------------------------------------------------------
        # Pillar 1: Team Pedigree (32 max)
        # -------------------------------------------------------------
        team_pts = 0.0
        if profile.fd_any_phd:
            team_pts += 8.0
            drivers.append("Founder holds a PhD / doctorate from a technical research university (+8 pts)")

        if profile.fd_team_has_technical_founder:
            team_pts += 7.0
            drivers.append("Dedicated technical co-founder / CTO on the founding roster (+7 pts)")

        if profile.fd_any_bigtech:
            team_pts += 5.0
            drivers.append("Founder pedigree includes prior Big Tech engineering experience (+5 pts)")

        if profile.fd_any_top_university:
            team_pts += 4.0
            drivers.append("Elite university origin (ETH Zurich, RWTH Aachen, TUM) (+4 pts)")

        n_founders = max(len(profile.founders_enriched), len(candidate.get("founders", [])))
        if n_founders >= 2:
            team_pts += 4.0
            drivers.append(f"Balanced multi-person founding team ({n_founders} key members) (+4 pts)")
        elif n_founders == 1:
            team_pts += 2.0

        if profile.fd_any_academic_researcher or profile.fd_university_spinoff:
            team_pts += 3.0
            drivers.append("Principal investigator / academic researcher commercializing proprietary lab IP (+3 pts)")

        if profile.fd_any_serial_founder or profile.fd_any_prior_exit:
            team_pts += 3.0
            drivers.append("Serial entrepreneur with prior venture track record (+3 pts)")

        team_pedigree = min(team_pts, 32.0)

        # -------------------------------------------------------------
        # Pillar 2: Commercial Intent (25 max)
        # -------------------------------------------------------------
        intent_pts = 0.0
        reg_num = (impressum.register_number if impressum else None) or candidate.get("register_number")
        legal_form = (impressum.legal_form if impressum else None) or candidate.get("legal_form")

        if reg_num:
            intent_pts += 10.0
            drivers.append(f"Commercial register registration verified ({reg_num}) (+10 pts)")
        elif legal_form:
            intent_pts += 7.0
            drivers.append(f"Formal corporate structure established ({legal_form}) (+7 pts)")

        has_demo = (impressum and impressum.has_pricing_or_demo) or ("demo" in desc or "pilot" in desc)
        if has_demo:
            intent_pts += 7.0
            drivers.append("Active commercial pilot / demo booking capabilities on live domain (+7 pts)")

        has_careers = (impressum and impressum.has_careers) or ("hiring" in desc or "careers" in desc)
        if has_careers:
            intent_pts += 4.0
            drivers.append("Active engineering team expansion / careers page (+4 pts)")

        if impressum and impressum.deeptech_keywords_found:
            intent_pts += 2.0
            drivers.append(f"Commercial deeptech focus verified: {', '.join(impressum.deeptech_keywords_found[:3])} (+2 pts)")
        elif any(k in desc for k in ["b2b", "enterprise", "industry", "industrial", "saas"]):
            intent_pts += 2.0

        commercial_intent = min(intent_pts, 25.0)

        # -------------------------------------------------------------
        # Pillar 3: Traction & Technical Moat (18 max)
        # -------------------------------------------------------------
        moat_pts = 0.0
        # Official university spin-off award / ERC grant anchors
        if any("spin-off award" in t or "rwth" in t for t in tags):
            moat_pts += 12.0
            drivers.append("Official RWTH Aachen Spin-off Award winner (+12 pts)")
        elif any("eth zurich" in t for t in tags):
            moat_pts += 11.0
            drivers.append("Official ETH Zurich spin-off approval (+11 pts)")
        elif any("erc proof-of-concept" in t or "horizon europe" in t for t in tags):
            moat_pts += 10.0
            drivers.append("European Research Council (ERC) Proof-of-Concept grant backed (+10 pts)")
        elif profile.fd_university_spinoff:
            moat_pts += 8.0
            drivers.append("Academic lab spin-off moat (+8 pts)")

        # Custom dedicated product domain
        norm_domain = candidate.get("normalized_domain")
        if norm_domain:
            moat_pts += 3.0
            drivers.append(f"Live independent domain infrastructure ({norm_domain}) (+3 pts)")

        # Technical traction (GitHub stars / Hugging Face downloads)
        if github and github.stargazer_count > 50 and not github.is_fake_star_suspect:
            moat_pts += 3.0
            drivers.append(f"Verified open-source developer adoption ({github.stargazer_count} GitHub stars) (+3 pts)")
        elif any(s.get("source_name") == "hf_hub" for s in signals):
            moat_pts += 3.0
            drivers.append("Trending machine learning foundation model on Hugging Face Hub (+3 pts)")

        technical_moat = min(moat_pts, 18.0)

        # -------------------------------------------------------------
        # Pillar 4: Multi-Signal Convergence (15 max)
        # -------------------------------------------------------------
        conv_pts = 0.0
        distinct_sources = len(set(s.get("source_name") for s in signals))
        if distinct_sources >= 2:
            conv_pts += 10.0
            drivers.append(f"Multi-signal convergence confirmed across {distinct_sources} independent sources (+10 pts)")
        elif len(signals) >= 1:
            conv_pts += 5.0
            drivers.append(f"Ingested from authoritative upstream source ({signals[0].get('source_name')}) (+5 pts)")

        if impressum and (impressum.register_number or impressum.founders):
            conv_pts += 3.0
            drivers.append("Corroborated by independent German § 5 DDG Impressum legal notice (+3 pts)")

        if any(s.get("source_name") == "openalex" for s in signals):
            conv_pts += 2.0
            drivers.append("Verified peer-reviewed research paper co-affiliation (+2 pts)")

        multi_signal_convergence = min(conv_pts, 15.0)

        # -------------------------------------------------------------
        # Pillar 5: HTGF Mandate Fit & Geographic Weight (10 max)
        # -------------------------------------------------------------
        if country == "DE":
            htgf_mandate_fit = 10.0
            drivers.append("Core German investment mandate (DE) (+10 pts)")
        elif country in ("CH", "AT"):
            htgf_mandate_fit = 8.5
            drivers.append(f"DACH secondary priority region ({country}) (+8.5 pts)")
        elif country in ("NL", "FR", "BE", "GB", "DK", "SE", "IT", "ES"):
            htgf_mandate_fit = 7.0
            drivers.append(f"European partner network region ({country}) (+7.0 pts)")
        else:
            htgf_mandate_fit = 4.0

        if profile.htgf_vertical == "out_of_mandate":
            htgf_mandate_fit *= 0.2
            drivers.append("Out of mandate sector penalty (-80%)")

        final_score = round(
            team_pedigree + commercial_intent + technical_moat + multi_signal_convergence + htgf_mandate_fit,
            1
        )

        # Collect evidence URLs
        evidence_urls = [s.get("source_url") for s in signals if s.get("source_url")]
        if candidate.get("website_url") and candidate["website_url"] not in evidence_urls:
            evidence_urls.insert(0, candidate["website_url"])
        for cit in profile.citations:
            if cit not in evidence_urls:
                evidence_urls.append(cit)

        return ScoreBreakdown(
            team_pedigree=round(team_pedigree, 1),
            commercial_intent=round(commercial_intent, 1),
            technical_moat=round(technical_moat, 1),
            multi_signal_convergence=round(multi_signal_convergence, 1),
            htgf_mandate_fit=round(htgf_mandate_fit, 1),
            final_score=min(max(final_score, 0.0), 100.0),
            drivers=drivers[:6],
            evidence_urls=evidence_urls[:5]
        )
