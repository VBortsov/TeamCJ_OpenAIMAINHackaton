"""
Enrichment package: Impressum scraping, GitHub validation, and LLM pedigree analysis.
"""

from backend.enrichment.impressum import ImpressumScraper, ImpressumResult
from backend.enrichment.github_verifier import GitHubVerifier, GitHubRepoMetrics

__all__ = [
    "ImpressumScraper",
    "ImpressumResult",
    "GitHubVerifier",
    "GitHubRepoMetrics",
]
