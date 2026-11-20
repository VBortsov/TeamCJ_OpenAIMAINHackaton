"""
Adapters package for signal sources.
"""

from backend.ingestion.adapters.eth_rwth import EthRwthAdapter
from backend.ingestion.adapters.openalex import OpenAlexAdapter
from backend.ingestion.adapters.hf_hub import HuggingFaceHubAdapter
from backend.ingestion.adapters.luma_events import LumaEventsAdapter

__all__ = [
    "EthRwthAdapter",
    "OpenAlexAdapter",
    "HuggingFaceHubAdapter",
    "LumaEventsAdapter",
]
