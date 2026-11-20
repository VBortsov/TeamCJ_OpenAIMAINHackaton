"""
Ingestion Package for Sourcing Signals.
"""

from backend.ingestion.base import BaseSignalAdapter, RawCandidate, RawFounder, RawSignal

__all__ = ["BaseSignalAdapter", "RawCandidate", "RawFounder", "RawSignal"]
