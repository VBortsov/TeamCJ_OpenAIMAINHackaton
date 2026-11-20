"""
GitHub Anti-Fake-Star & Open-Source Technical Moat Verifier.
Queries GitHub API (respecting unauth 60/hr or GITHUB_TOKEN 5000/hr) to evaluate
stargazer counts, fork-to-star ratio, recency of commits, and licensing.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.config import settings
from backend.ingestion.http_client import CachedHttpClient

logger = logging.getLogger(__name__)


class GitHubRepoMetrics(BaseModel):
    org_or_owner: str
    top_repo_name: Optional[str] = None
    stargazer_count: int = 0
    fork_count: int = 0
    fork_to_star_ratio: float = 0.0
    pushed_at: Optional[str] = None
    license_spdx_id: Optional[str] = None
    primary_language: Optional[str] = None
    open_issues_count: int = 0
    is_fake_star_suspect: bool = False  # True if stars >= 100 and fork_to_star_ratio < 0.03


class FounderGitHubProfile(BaseModel):
    username: str
    profile_url: str
    name: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    blog: Optional[str] = None
    public_repos: int = 0
    followers: int = 0
    total_stars: int = 0
    total_forks: int = 0
    top_languages: List[str] = Field(default_factory=list)
    top_repos: List[Dict[str, Any]] = Field(default_factory=list)
    social_accounts: List[Dict[str, Any]] = Field(default_factory=list)


class GitHubVerifier:
    """Verifies open-source technical traction and screens out bot/purchased GitHub stars."""

    def __init__(self, http_client: Optional[CachedHttpClient] = None):
        self.http_client = http_client

    async def verify(self, org_or_repo: str) -> Optional[GitHubRepoMetrics]:
        if not org_or_repo:
            return None

        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        clean_slug = org_or_repo.strip("/").replace("https://github.com/", "")
        parts = [p for p in clean_slug.split("/") if p]
        if not parts:
            return None

        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

        metrics = GitHubRepoMetrics(org_or_owner=parts[0])

        try:
            if own_client:
                await client.__aenter__()

            if len(parts) >= 2:
                # Specific repo: owner/repo
                repo_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
                res = await client.get_json(repo_url, headers=headers, use_cache=True)
                metrics.top_repo_name = res.get("name")
                metrics.stargazer_count = res.get("stargazers_count", 0)
                metrics.fork_count = res.get("forks_count", 0)
                metrics.pushed_at = res.get("pushed_at")
                metrics.primary_language = res.get("language")
                metrics.open_issues_count = res.get("open_issues_count", 0)
                lic = res.get("license") or {}
                metrics.license_spdx_id = lic.get("spdx_id")
            else:
                # Org or user: query repos sorted by stars
                org_url = f"https://api.github.com/orgs/{parts[0]}/repos?sort=pushed&direction=desc&per_page=10"
                try:
                    repos = await client.get_json(org_url, headers=headers, use_cache=True)
                except Exception:
                    # Try user repos if org returns 404
                    user_url = f"https://api.github.com/users/{parts[0]}/repos?sort=pushed&direction=desc&per_page=10"
                    repos = await client.get_json(user_url, headers=headers, use_cache=True)

                if isinstance(repos, list) and repos:
                    # Find repo with highest stars
                    best_repo = max(repos, key=lambda r: r.get("stargazers_count", 0))
                    metrics.top_repo_name = best_repo.get("name")
                    metrics.stargazer_count = sum(r.get("stargazers_count", 0) for r in repos)
                    metrics.fork_count = sum(r.get("forks_count", 0) for r in repos)
                    metrics.pushed_at = best_repo.get("pushed_at")
                    metrics.primary_language = best_repo.get("language")
                    metrics.open_issues_count = sum(r.get("open_issues_count", 0) for r in repos)
                    lic = best_repo.get("license") or {}
                    metrics.license_spdx_id = lic.get("spdx_id")

            # Calculate fork-to-star ratio
            if metrics.stargazer_count > 0:
                metrics.fork_to_star_ratio = round(metrics.fork_count / metrics.stargazer_count, 4)

            # Gate 4 Rule: stars >= 100 AND fork_to_star_ratio < 0.03
            if metrics.stargazer_count >= 100 and metrics.fork_to_star_ratio < 0.03:
                metrics.is_fake_star_suspect = True

        except Exception as e:
            logger.debug("GitHub verifier error for %s: %s", org_or_repo, e)
            return None
        finally:
            if own_client:
                await client.__aexit__(None, None, None)

        return metrics

    async def verify_founder(self, username: str) -> Optional[FounderGitHubProfile]:
        """
        Fetches public GitHub founder profile, repository track record, and social links.
        Strictly zero guessing: returns None if the user does not exist on GitHub.
        """
        if not username:
            return None

        clean_user = username.strip().lstrip("@").replace("https://github.com/", "").strip("/")
        if not clean_user or "/" in clean_user:
            return None

        client = self.http_client or CachedHttpClient()
        own_client = self.http_client is None

        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

        try:
            if own_client:
                await client.__aenter__()

            user_url = f"https://api.github.com/users/{clean_user}"
            try:
                user_data = await client.get_json(user_url, headers=headers, use_cache=True)
            except Exception as e:
                logger.debug("GitHub user %s not found: %s", clean_user, e)
                return None

            if not isinstance(user_data, dict) or not user_data.get("login"):
                return None

            # Fetch recent repos
            repos_url = f"https://api.github.com/users/{clean_user}/repos?sort=pushed&per_page=10"
            try:
                repos_data = await client.get_json(repos_url, headers=headers, use_cache=True)
                if not isinstance(repos_data, list):
                    repos_data = []
            except Exception:
                repos_data = []

            # Fetch social accounts
            socials_url = f"https://api.github.com/users/{clean_user}/social_accounts"
            try:
                socials_data = await client.get_json(socials_url, headers=headers, use_cache=True)
                if not isinstance(socials_data, list):
                    socials_data = []
            except Exception:
                socials_data = []

            total_stars = sum(r.get("stargazers_count", 0) for r in repos_data)
            total_forks = sum(r.get("forks_count", 0) for r in repos_data)

            # Extract distinct top languages
            languages = []
            for r in repos_data:
                lang = r.get("language")
                if lang and lang not in languages:
                    languages.append(lang)

            # Sort repos by stars descending
            sorted_repos = sorted(repos_data, key=lambda r: r.get("stargazers_count", 0), reverse=True)
            top_repos = [
                {
                    "name": r.get("name"),
                    "url": r.get("html_url"),
                    "stars": r.get("stargazers_count", 0),
                    "forks": r.get("forks_count", 0),
                    "language": r.get("language"),
                    "pushed_at": r.get("pushed_at")
                }
                for r in sorted_repos[:5]
            ]

            clean_socials = [
                {
                    "provider": s.get("provider", "other"),
                    "url": s.get("url", "")
                }
                for s in socials_data if isinstance(s, dict) and s.get("url")
            ]

            return FounderGitHubProfile(
                username=user_data.get("login", clean_user),
                profile_url=user_data.get("html_url", f"https://github.com/{clean_user}"),
                name=user_data.get("name"),
                bio=user_data.get("bio"),
                company=user_data.get("company"),
                location=user_data.get("location"),
                blog=user_data.get("blog") or None,
                public_repos=user_data.get("public_repos", len(repos_data)),
                followers=user_data.get("followers", 0),
                total_stars=total_stars,
                total_forks=total_forks,
                top_languages=languages,
                top_repos=top_repos,
                social_accounts=clean_socials
            )

        except Exception as e:
            logger.debug("Error verifying founder github profile %s: %s", clean_user, e)
            return None
        finally:
            if own_client:
                await client.__aexit__(None, None, None)
