"""
Adapter for Hugging Face Hub: Discovers trending AI startups, open-weights labs,
and model creators via Hugging Face Hub APIs.
"""

from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional, Set

from backend.config import settings, BIG_TECH_BLOCKLIST
from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal
from backend.ingestion.http_client import CachedHttpClient
from backend.db.repository import normalize_domain

logger = logging.getLogger(__name__)

HF_MODELS_URL = "https://huggingface.co/api/models"
HF_ORGS_URL = "https://huggingface.co/api/organizations"

# Additional well-known big tech or research conglomerates on Hugging Face
HF_EXCLUDED_ORGS: Set[str] = {
    "google", "meta", "meta-llama", "facebook", "microsoft", "apple", "nvidia",
    "amazon", "aws", "ibm", "intel", "amd", "alibaba", "alibabacloud", "tencent",
    "baidu", "bytedance", "deepseek-ai", "mistralai", "openai", "anthropic",
    "stabilityai", "cohere", "huggingface", "huggingfacetb", "qwen"
}


def is_excluded_hf_org(org_name: str) -> bool:
    norm = org_name.lower().strip()
    if norm in HF_EXCLUDED_ORGS:
        return True
    for blocked in BIG_TECH_BLOCKLIST:
        if blocked == norm or norm.startswith(f"{blocked}-"):
            return True
    return False


class HuggingFaceHubAdapter(BaseSignalAdapter):
    """Ingests trending AI model developers and spin-offs from Hugging Face Hub."""

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        super().__init__(name="hf_hub")
        self.http_client = http_client

    async def fetch(self, limit: int = 40) -> List[RawCandidate]:
        candidates: List[RawCandidate] = []
        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        headers = {}
        if settings.HF_TOKEN:
            headers["Authorization"] = f"Bearer {settings.HF_TOKEN}"

        params = {
            "sort": "trendingScore",
            "direction": "-1",
            "limit": 100,
            "full": "true"
        }

        try:
            if own_client:
                await client.__aenter__()

            logger.info("Fetching trending models from Hugging Face Hub...")
            models = await client.get_json(HF_MODELS_URL, params=params, headers=headers or None, use_cache=True)
            logger.info("Retrieved %d models from Hugging Face", len(models))

            # Group models by organization / namespace
            org_models: Dict[str, List[Dict[str, Any]]] = {}
            for m in models:
                mid = m.get("id", "")
                if "/" not in mid:
                    continue  # skip individual standalone accounts without org prefix
                org = mid.split("/")[0].strip()
                if not org or is_excluded_hf_org(org):
                    continue

                if org not in org_models:
                    org_models[org] = []
                org_models[org].append(m)

            logger.info("Discovered %d non-Big-Tech AI organizations on HF Hub", len(org_models))

            for org_name, m_list in org_models.items():
                if len(candidates) >= limit:
                    break

                # Sort models by trendingScore and downloads
                top_model = m_list[0]
                top_model_id = top_model.get("id", "")
                trending_score = top_model.get("trendingScore", 0.0)
                downloads = top_model.get("downloads", 0)
                likes = top_model.get("likes", 0)
                tags = top_model.get("tags", [])
                arxiv_tags = [t for t in tags if t.startswith("arxiv:")]

                # Fetch Org Overview
                org_meta = {}
                try:
                    org_meta = await client.get_json(
                        f"{HF_ORGS_URL}/{org_name}/overview",
                        headers=headers or None,
                        use_cache=True
                    )
                except Exception as ex:
                    logger.debug("Failed fetching overview for HF org %s: %s", org_name, ex)

                # Fetch Org Members
                members = []
                try:
                    members = await client.get_json(
                        f"{HF_ORGS_URL}/{org_name}/members",
                        headers=headers or None,
                        use_cache=True
                    )
                except Exception as ex:
                    logger.debug("Failed fetching members for HF org %s: %s", org_name, ex)

                org_fullname = org_meta.get("fullname") or org_name
                canonical_name = org_fullname if len(org_fullname) > 2 else org_name
                hf_url = f"https://huggingface.co/{org_name}"
                custom_site = org_meta.get("website") or org_meta.get("homepage")
                effective_website = custom_site if custom_site and "http" in custom_site else hf_url
                norm_domain = normalize_domain(custom_site) if custom_site else None

                founders: List[RawFounder] = []
                if isinstance(members, list):
                    for mem in members[:4]:
                        user_handle = mem.get("user")
                        mem_name = mem.get("fullname") or user_handle
                        if user_handle:
                            founders.append(
                                RawFounder(
                                    full_name=mem_name or user_handle,
                                    hf_handle=user_handle,
                                    link_method="hf_org_member",
                                    link_confidence=0.8
                                )
                            )

                signal = RawSignal(
                    source_name=self.name,
                    source_native_id=f"hf:model:{top_model_id}",
                    signal_date=top_model.get("lastModified", None),
                    raw_metrics={
                        "org": org_name,
                        "top_model_id": top_model_id,
                        "trending_score": trending_score,
                        "downloads": downloads,
                        "likes": likes,
                        "arxiv_tags": arxiv_tags,
                        "org_model_count": org_meta.get("numModels", len(m_list)),
                        "org_num_users": org_meta.get("numUsers", len(members) if isinstance(members, list) else 0)
                    },
                    source_url=f"https://huggingface.co/{top_model_id}"
                )

                candidate = RawCandidate(
                    canonical_name=canonical_name,
                    normalized_domain=norm_domain,
                    website_url=effective_website,
                    country_code=None,  # HF doesn't guarantee country, can be enriched downstream
                    description=(
                        f"AI research lab & startup developing open-weights models on Hugging Face. "
                        f"Top model '{top_model_id}' with trending score {trending_score} "
                        f"and {downloads:,} downloads. Key papers: {', '.join(arxiv_tags) if arxiv_tags else 'N/A'}."
                    ),
                    raw_tags=["AI/ML", "Foundation Models", "OpenWeights", "Hugging Face"] + arxiv_tags[:3],
                    funding_status="unknown",
                    source_name=self.name,
                    source_native_id=f"hf:org:{org_name}",
                    signals=[signal],
                    founders=founders
                )
                candidates.append(candidate)

        except Exception as e:
            logger.error("Failed fetching Hugging Face Hub models: %s", e, exc_info=True)
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return candidates[:limit]
