"""
Base models and abstract adapter contract for Wave 1 ingestion.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RawSignal(BaseModel):
    """Signal event ingested from a specific source."""
    source_name: str = Field(description="Name of source adapter: eth_rwth, openalex, hf_hub, luma_events")
    source_native_id: str = Field(description="Upstream ID, slug, DOI, or model ID")
    signal_date: Optional[str] = Field(default=None, description="ISO date or timestamp (YYYY-MM-DD)")
    raw_metrics: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary raw metrics dictionary")
    source_url: str = Field(description="URL to the source item for provenance")


class RawFounder(BaseModel):
    """Researcher, academic founder, or technical executive linked to an entity."""
    full_name: str
    orcid_id: Optional[str] = None
    openalex_author_id: Optional[str] = None
    github_handle: Optional[str] = None
    hf_handle: Optional[str] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    h_index: Optional[int] = None
    cited_by_count: Optional[int] = None
    ex_big_tech_tags: List[str] = Field(default_factory=list)
    elite_affiliations: List[str] = Field(default_factory=list)
    raw_bio: Optional[str] = None
    link_method: str = Field(default="source_roster", description="e.g. paper_coauthorship, hf_org_member, spin_off_roster, luma_host")
    link_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    is_geschaeftsfuehrer: int = Field(default=0, ge=0, le=1)


class RawCandidate(BaseModel):
    """
    Standardized startup / spin-off candidate object emitted by adapters
    before database persistence.
    """
    canonical_name: str
    normalized_domain: Optional[str] = None
    website_url: Optional[str] = None
    legal_name: Optional[str] = None
    legal_form: Optional[str] = None
    register_number: Optional[str] = None
    country_code: Optional[str] = None  # DE, CH, AT, NL, etc.
    city: Optional[str] = None
    incorporation_date: Optional[str] = None
    domain_created_date: Optional[str] = None
    description: Optional[str] = None
    raw_tags: List[str] = Field(default_factory=list)
    funding_status: str = Field(default="unknown")
    lp_synergy: Optional[Dict[str, Any]] = None
    source_name: Optional[str] = None
    source_native_id: Optional[str] = None
    signals: List[RawSignal] = Field(default_factory=list)
    founders: List[RawFounder] = Field(default_factory=list)


class BaseSignalAdapter(ABC):
    """Abstract base class for all signal adapters."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        """
        Fetch up to `limit` candidates from the upstream source.
        Must handle its own internal errors gracefully and return
        valid RawCandidate instances.
        """
        pass
