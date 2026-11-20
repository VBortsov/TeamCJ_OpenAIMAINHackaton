"""
Deterministic Kill-Gates Evaluation (Zero-LLM Cost).
Screens raw candidates against 5 non-negotiable early-stage VC & HTGF criteria.
"""

from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.config import BIG_TECH_BLOCKLIST
from backend.db.repository import DatabaseRepository, slugify

logger = logging.getLogger(__name__)

# Gate 1: Homework, Hobby, Coursework, Event Networks
GATE1_PATTERNS = [
    re.compile(r"(?i)\b(community|communities|network|women in tech|club|meetup|association|society|chapter|cohort|events|summit|conference|court)\b"),
    re.compile(r"(?i)\b(tutorial|course|homework|assignment|curated list|awesome-|starter kit|cheatsheet|demo-day attendee)\b"),
]

# Gate 2: Dev Agencies, Consultancies, Freelancers (without proprietary IP/product)
GATE2_PATTERNS = [
    re.compile(r"(?i)\b(unternehmensberatung|it-beratung|consulting|agentur|werbeagentur|digitalagentur|freelance|staff augmentation|outsourcing|rechtsanwalt|kanzlei)\b"),
]

PRODUCT_IP_PATTERNS = [
    re.compile(r"(?i)\b(platform|software|saas|api|hardware|sensor|patent|deeptech|algorithm|model|spin-off|ai-based|ai-powered)\b"),
]

# Gate 5: Explicit Series A+ Mention Patterns
FUNDED_EXCLUSION_PATTERNS = [
    re.compile(r"(?i)\b(series\s+[a-z]|series\s+[1-9]|ipo|public company|acquired by|unicorn|decacorn)\b"),
    re.compile(r"(?i)\b(?:raised|secures|closed)\s+(?:€|\$|£)?(?:[3-9]\d|\d{3,})\s*(?:M|million|B|billion)\b"),
]


class KillGateResult(BaseModel):
    entity_id: str
    canonical_name: str
    gate_passed: bool
    kill_reasons: List[str] = Field(default_factory=list)
    gate1_passed: bool = True
    gate2_passed: bool = True
    gate3_passed: bool = True
    gate4_passed: bool = True
    gate5_passed: bool = True


class KillGateEvaluator:
    """Evaluates 5 deterministic Kill-Gates prior to any LLM scoring."""

    def __init__(self, repo: DatabaseRepository):
        self.repo = repo

    def evaluate(
        self,
        candidate: Dict[str, Any],
        impressum_legal_name: Optional[str] = None,
        impressum_text: Optional[str] = None,
        github_stars: int = 0,
        github_fork_ratio: float = 0.0
    ) -> KillGateResult:
        ent_id = candidate.get("entity_id", "")
        name = candidate.get("canonical_name", "")
        domain = candidate.get("normalized_domain") or candidate.get("website_url")
        desc = candidate.get("description", "") or ""
        legal_name = impressum_legal_name or candidate.get("legal_name", "") or ""
        tags = " ".join(candidate.get("raw_tags", []))
        full_text = f"{name} {desc} {tags} {legal_name} {impressum_text or ''}"

        reasons: List[str] = []
        g1 = g2 = g3 = g4 = g5 = True

        # =========================================================================
        # Gate 1: Homework / Hobby / Community / Meetup
        # =========================================================================
        for pat in GATE1_PATTERNS:
            if pat.search(name) or pat.search(desc[:120]):
                g1 = False
                reasons.append(f"Gate 1 (Hobby/Community): Matched pattern '{pat.pattern}' in name/description")
                break

        # =========================================================================
        # Gate 2: Dev Agency / Consultancy (unless strong product/IP markers exist)
        # =========================================================================
        has_agency = any(pat.search(full_text) for pat in GATE2_PATTERNS)
        has_product_ip = any(pat.search(full_text) for pat in PRODUCT_IP_PATTERNS)
        if has_agency and not has_product_ip:
            g2 = False
            reasons.append("Gate 2 (Dev Agency / Consultancy): Identified as consultancy/agency without product/IP moat")

        # =========================================================================
        # Gate 3: Big Tech / Legacy Corporate / Company Age > 3 Years
        # =========================================================================
        name_lower = name.lower()
        legal_lower = legal_name.lower()
        for blocked in BIG_TECH_BLOCKLIST:
            if blocked in name_lower or (legal_lower and blocked in legal_lower):
                g3 = False
                reasons.append(f"Gate 3 (Legacy Corporate): Matches corporate blocklist token '{blocked}'")
                break

        # Age check: if incorporation date or approval year is prior to 2023 (HTGF mandate ceiling: <=3 years)
        inc_date = candidate.get("incorporation_date")
        if inc_date and re.search(r"^(?:19\d\d|200\d|201\d|202[0-2])", inc_date):
            g3 = False
            reasons.append(f"Gate 3 (Age Limit): Incorporation year {inc_date[:4]} exceeds HTGF <= 3.0 yrs mandate")

        # =========================================================================
        # Gate 4: StarScout Fake-Star Anomaly (stars >= 100 & fork_ratio < 0.03)
        # =========================================================================
        has_academic_anchor = any(
            t in tags for t in ["ETH Zurich", "RWTH Aachen", "ERC Proof-of-Concept", "Research Co-Affiliation"]
        )
        if github_stars >= 100 and github_fork_ratio < 0.03 and not has_academic_anchor:
            g4 = False
            reasons.append(f"Gate 4 (Fake-Star Anomaly): GitHub stars={github_stars} with suspicious fork ratio={github_fork_ratio}")

        # =========================================================================
        # Gate 5: Already Funded Seed+ / Series A+ Exclusion
        # =========================================================================
        # Check SQLite funding blocklist (populated from tech feeds & YC <= 2024)
        is_blocked, bl_reason = self.repo.check_funding_blocklist(name, domain)
        if is_blocked:
            g5 = False
            reasons.append(f"Gate 5 (Already Funded): {bl_reason}")
        else:
            for pat in FUNDED_EXCLUSION_PATTERNS:
                if pat.search(desc):
                    g5 = False
                    reasons.append(f"Gate 5 (Explicit Series A+): Description mentions scaleup funding ('{pat.pattern}')")
                    break

        all_passed = g1 and g2 and g3 and g4 and g5
        return KillGateResult(
            entity_id=ent_id,
            canonical_name=name,
            gate_passed=all_passed,
            kill_reasons=reasons,
            gate1_passed=g1,
            gate2_passed=g2,
            gate3_passed=g3,
            gate4_passed=g4,
            gate5_passed=g5
        )
