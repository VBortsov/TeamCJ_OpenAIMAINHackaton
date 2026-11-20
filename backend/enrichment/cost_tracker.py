"""
Cost Tracking, Budget Circuit-Breaker & Cache Monitoring Module.
Monitors token usage, live OpenAI calls, and estimated spend in USD.
Enforces hard circuit-breaker to strictly prevent budget overruns.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import settings

logger = logging.getLogger(__name__)

# Official OpenAI gpt-4o-mini + web_search_preview pricing constants (Estimated USD)
PRICE_INPUT_PER_1M = 0.15
PRICE_OUTPUT_PER_1M = 0.60
PRICE_WEB_SEARCH_CALL = 0.025  # $0.025 per web search tool execution

LEDGER_FILENAME = "cost_ledger.jsonl"


class CostTracker:
    """Tracks live OpenAI calls, tokens, search calls, and enforces spend budget."""

    def __init__(
        self,
        max_budget_usd: float = 2.00,
        cache_only: bool = False,
        ledger_path: Optional[Path | str] = None
    ):
        self.max_budget_usd = max_budget_usd
        self.cache_only = cache_only
        self.ledger_path = Path(ledger_path or settings.DATA_DIR / LEDGER_FILENAME)

        # Per-session metrics
        self.cache_hits: int = 0
        self.live_llm_calls: int = 0
        self.web_search_calls: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.estimated_cost_usd: float = 0.0

    def record_cache_hit(self, entity_id: str, canonical_name: str) -> None:
        """Records a successful read from disk cache ($0.00 cost)."""
        self.cache_hits += 1

    def can_execute_live_call(self, expected_cost: float = 0.03) -> bool:
        """
        Circuit-breaker check. Returns False if cache_only mode is on
        or if the session budget cap is reached.
        """
        if self.cache_only:
            logger.warning(
                "CACHE-ONLY MODE ACTIVE: Refusing live OpenAI call for new lead."
            )
            return False

        if (self.estimated_cost_usd + expected_cost) > self.max_budget_usd:
            logger.critical(
                "BUDGET CAP REACHED: Current session spend $%.4f + $%.4f exceeds cap $%.2f! Halting live calls.",
                self.estimated_cost_usd, expected_cost, self.max_budget_usd
            )
            return False

        return True

    def record_live_call(
        self,
        entity_id: str,
        canonical_name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        web_search_calls: int = 0
    ) -> float:
        """
        Records a completed live OpenAI call, calculates estimated cost,
        and appends to data/cost_ledger.jsonl.
        """
        call_cost = (
            (input_tokens / 1_000_000) * PRICE_INPUT_PER_1M
            + (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_1M
            + (web_search_calls * PRICE_WEB_SEARCH_CALL)
        )

        self.live_llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.web_search_calls += web_search_calls
        self.estimated_cost_usd += call_cost

        # Append to audit ledger
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "entity_id": entity_id,
                "canonical_name": canonical_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "web_search_calls": web_search_calls,
                "estimated_cost_usd": round(call_cost, 6)
            }
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("Failed writing to cost ledger: %s", e)

        return call_cost

    def get_cumulative_ledger_spend(self) -> float:
        """Reads cumulative historical spend across all runs from data/cost_ledger.jsonl."""
        if not self.ledger_path.exists():
            return 0.0
        total = 0.0
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        total += float(data.get("estimated_cost_usd", 0.0))
        except Exception as e:
            logger.debug("Error reading cost ledger: %s", e)
        return round(total, 4)

    def get_summary(self) -> Dict[str, Any]:
        """Returns consolidated cost and cache statistics."""
        return {
            "cache_hits": self.cache_hits,
            "live_llm_calls": self.live_llm_calls,
            "web_search_calls": self.web_search_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_run_cost_usd": round(self.estimated_cost_usd, 4),
            "cumulative_ledger_spend_usd": self.get_cumulative_ledger_spend(),
            "max_budget_usd": self.max_budget_usd,
            "cache_only": self.cache_only
        }
