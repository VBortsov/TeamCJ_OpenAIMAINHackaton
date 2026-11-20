"""
Adapters package for signal sources.
"""

from backend.ingestion.adapters.eth_rwth import EthRwthAdapter
from backend.ingestion.adapters.openalex import OpenAlexAdapter
from backend.ingestion.adapters.hf_hub import HuggingFaceHubAdapter
from backend.ingestion.adapters.luma_events import LumaEventsAdapter
from backend.ingestion.adapters.cordis_erc import CordisErcAdapter
from backend.ingestion.adapters.funding_blocklist import FundingBlocklistAdapter
from backend.ingestion.adapters.yc_recent import YcRecentAdapter

__all__ = [
    "EthRwthAdapter",
    "OpenAlexAdapter",
    "HuggingFaceHubAdapter",
    "LumaEventsAdapter",
    "CordisErcAdapter",
    "FundingBlocklistAdapter",
    "YcRecentAdapter",
]
