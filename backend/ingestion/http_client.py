"""
Robust Async HTTP Client with Disk Caching and Tenacity Retry.
Supports rate-limit backoff (HTTP 429 Retry-After) and concurrent request throttling.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

from backend.config import settings

logger = logging.getLogger(__name__)


class CachedHttpClient:
    """
    Async HTTP client with disk caching for high performance and polite scraping.
    """

    def __init__(
        self,
        cache_dir: Optional[Path | str] = None,
        cache_enabled: Optional[bool] = None,
        max_concurrency: Optional[int] = None,
        timeout: Optional[float] = None,
        user_agent: Optional[str] = None
    ):
        self.cache_dir = Path(cache_dir or settings.HTTP_CACHE_DIR)
        self.cache_enabled = cache_enabled if cache_enabled is not None else settings.HTTP_CACHE_ENABLED
        self.max_concurrency = max_concurrency or settings.MAX_CONCURRENT_REQUESTS
        self.timeout = timeout or settings.REQUEST_TIMEOUT_SECONDS
        self.user_agent = user_agent or settings.USER_AGENT

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> CachedHttpClient:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
            "Accept-Encoding": "identity",
        }
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _cache_key(self, method: str, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        param_str = ""
        if params:
            sorted_params = sorted((str(k), str(v)) for k, v in params.items())
            param_str = json.dumps(sorted_params, sort_keys=True)
        raw = f"{method.upper()}|{url}|{param_str}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _read_cache(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.cache_enabled:
            return None
        cache_path = self.cache_dir / f"{key}.json"
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            logger.debug("Failed reading cache %s: %s", cache_path, e)
            return None

    def _write_cache(self, key: str, data: Dict[str, Any]) -> None:
        if not self.cache_enabled:
            return
        cache_path = self.cache_dir / f"{key}.json"
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.debug("Failed writing cache %s: %s", cache_path, e)

    async def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True
    ) -> httpx.Response:
        """
        Execute request with disk cache, semaphore, tenacity retry, and 429 backoff.
        """
        key = self._cache_key(method, url, params)

        if use_cache:
            cached = self._read_cache(key)
            if cached and cached.get("status_code", 0) in (200, 201, 202, 204):
                req = httpx.Request(method, url, params=params)
                res = httpx.Response(
                    status_code=cached["status_code"],
                    headers=cached.get("headers", {}),
                    text=cached.get("text", ""),
                    request=req
                )
                logger.debug("Disk HTTP cache HIT: %s %s", method, url)
                return res

        if not self._client:
            raise RuntimeError("CachedHttpClient must be used within an 'async with' context")

        max_attempts = 4
        attempt = 0
        last_exception = None

        while attempt < max_attempts:
            attempt += 1
            try:
                async with self._semaphore:
                    logger.debug("Executing HTTP request: %s %s (attempt %d/%d)", method, url, attempt, max_attempts)
                    res = await self._client.request(
                        method=method,
                        url=url,
                        params=params,
                        headers=headers
                    )

                # Rate Limit handling (429)
                if res.status_code == 429:
                    retry_after_hdr = res.headers.get("Retry-After")
                    wait_seconds = 2.0
                    if retry_after_hdr:
                        try:
                            wait_seconds = float(retry_after_hdr)
                        except ValueError:
                            wait_seconds = 3.0
                    wait_seconds = min(max(wait_seconds, 1.0), 15.0)
                    logger.warning("HTTP 429 Rate Limit on %s. Backing off for %.1fs (attempt %d)", url, wait_seconds, attempt)
                    await asyncio.sleep(wait_seconds)
                    continue

                # Server Error retries (5xx)
                if res.status_code >= 500:
                    wait_seconds = min(2.0 ** attempt, 8.0)
                    logger.warning("HTTP %d from %s. Retrying in %.1fs...", res.status_code, url, wait_seconds)
                    await asyncio.sleep(wait_seconds)
                    continue

                # Success or client error (4xx) -> write cache for 2xx
                if res.status_code < 400 and use_cache:
                    cache_payload = {
                        "status_code": res.status_code,
                        "headers": dict(res.headers),
                        "text": res.text,
                        "cached_at": time.time(),
                        "url": str(res.url)
                    }
                    self._write_cache(key, cache_payload)

                return res

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                wait_seconds = min(2.0 ** attempt, 8.0)
                logger.warning("Network/Timeout error requesting %s (%s). Retrying in %.1fs...", url, e, wait_seconds)
                await asyncio.sleep(wait_seconds)

        if last_exception:
            raise last_exception
        raise httpx.HTTPStatusError(
            f"Exceeded max retries for {url}",
            request=httpx.Request(method, url),
            response=res
        )

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True
    ) -> httpx.Response:
        return await self.request("GET", url, params=params, headers=headers, use_cache=use_cache)

    async def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True
    ) -> Any:
        res = await self.get(url, params=params, headers=headers, use_cache=use_cache)
        res.raise_for_status()
        return res.json()

    async def get_text(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True
    ) -> str:
        res = await self.get(url, params=params, headers=headers, use_cache=use_cache)
        res.raise_for_status()
        return res.text
