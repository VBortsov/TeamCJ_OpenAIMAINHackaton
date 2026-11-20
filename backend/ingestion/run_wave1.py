"""
Wave 1 Ingestion Pipeline Runner:
Executes ETH/RWTH, OpenAlex, Hugging Face Hub, and Luma Events adapters,
persists records to SQLite (data/scout.db), exports data/wave1_candidates.json,
and prints a diagnostic verification summary.
"""

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import List
from tabulate import tabulate

from backend.config import settings
from backend.db.repository import DatabaseRepository
from backend.ingestion.base import BaseSignalAdapter, RawCandidate
from backend.ingestion.http_client import CachedHttpClient
from backend.ingestion.adapters import (
    EthRwthAdapter,
    OpenAlexAdapter,
    HuggingFaceHubAdapter,
    LumaEventsAdapter,
)

# Configure readable logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("run_wave1")


async def run_pipeline(limit_per_adapter: int = 40) -> DatabaseRepository:
    """Run full Wave 1 ingestion against all 4 adapters."""
    start_time = time.time()
    logger.info("================================================================================")
    logger.info("Starting Wave 1 Ingestion: Early-Stage European VC Startup Sourcing Engine")
    logger.info("Target: MAIN Summit 2026 - HTGF Track (Focus: ~70%% DACH, Pre-Seed/Seed DeepTech)")
    logger.info("================================================================================")

    # 1. Initialize SQLite Database
    repo = DatabaseRepository(settings.SQLITE_DB_PATH)

    # 2. Instantiate Adapters sharing a single cached HTTP client
    async with CachedHttpClient() as http_client:
        adapters: List[BaseSignalAdapter] = [
            EthRwthAdapter(http_client=http_client),
            OpenAlexAdapter(http_client=http_client),
            HuggingFaceHubAdapter(http_client=http_client),
            LumaEventsAdapter(http_client=http_client),
        ]

        logger.info("Running %d adapters with limit=%d each...", len(adapters), limit_per_adapter)

        # Run adapters concurrently
        results = await asyncio.gather(
            *[adapter.fetch(limit=limit_per_adapter) for adapter in adapters],
            return_exceptions=True
        )

        total_ingested_candidates = 0

        # 3. Persist to Database
        for adapter, res in zip(adapters, results):
            if isinstance(res, Exception):
                logger.error("Adapter '%s' failed with error: %s", adapter.name, res)
                continue

            candidates: List[RawCandidate] = res
            logger.info("Persisting %d candidates from '%s'...", len(candidates), adapter.name)

            for cand in candidates:
                # Upsert entity
                entity_id = repo.upsert_entity(
                    canonical_name=cand.canonical_name,
                    normalized_domain=cand.normalized_domain,
                    website_url=cand.website_url,
                    legal_name=cand.legal_name,
                    legal_form=cand.legal_form,
                    register_number=cand.register_number,
                    country_code=cand.country_code,
                    incorporation_date=cand.incorporation_date,
                    domain_created_date=cand.domain_created_date,
                    description=cand.description,
                    raw_tags=cand.raw_tags,
                    funding_status=cand.funding_status,
                    lp_synergy=cand.lp_synergy,
                    source_name=cand.source_name,
                    source_native_id=cand.source_native_id
                )

                # Upsert signals
                for sig in cand.signals:
                    repo.upsert_signal(
                        entity_id=entity_id,
                        source_name=sig.source_name,
                        source_native_id=sig.source_native_id,
                        signal_date=sig.signal_date,
                        raw_metrics=sig.raw_metrics,
                        source_url=sig.source_url
                    )

                # Upsert founders and create links
                for fnd in cand.founders:
                    founder_id = repo.upsert_founder(
                        full_name=fnd.full_name,
                        orcid_id=fnd.orcid_id,
                        openalex_author_id=fnd.openalex_author_id,
                        github_handle=fnd.github_handle,
                        hf_handle=fnd.hf_handle,
                        linkedin_url=fnd.linkedin_url,
                        email=fnd.email,
                        h_index=fnd.h_index,
                        cited_by_count=fnd.cited_by_count,
                        ex_big_tech_tags=fnd.ex_big_tech_tags,
                        elite_affiliations=fnd.elite_affiliations,
                        raw_bio=fnd.raw_bio
                    )
                    repo.link_entity_founder(
                        entity_id=entity_id,
                        founder_id=founder_id,
                        link_method=fnd.link_method,
                        link_confidence=fnd.link_confidence,
                        is_geschaeftsfuehrer=fnd.is_geschaeftsfuehrer
                    )

                total_ingested_candidates += 1

    duration = time.time() - start_time
    logger.info("Ingestion completed in %.2f seconds.", duration)

    # 4. Export clean JSON snapshot for teammates (UI, scoring, n8n)
    export_path = settings.DATA_DIR / "wave1_candidates.json"
    candidates_export = repo.export_all_candidates()
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(candidates_export, f, indent=2, ensure_ascii=False)
    logger.info("Exported %d candidates to %s", len(candidates_export), export_path)

    # 5. Print Diagnostic Verification Summary
    print_diagnostic_summary(repo, candidates_export)

    return repo


def print_diagnostic_summary(repo: DatabaseRepository, candidates: List[dict]) -> None:
    """Print structured diagnostic verification tables."""
    stats = repo.get_stats()

    print("\n" + "=" * 80)
    print("                    WAVE 1 INGESTION VERIFICATION SUMMARY")
    print("=" * 80)

    # 1. Overall Database Stats Table
    overview_table = [
        ["Total Entities (Startups)", stats["total_entities"]],
        ["Total Signals Recorded", stats["total_signals"]],
        ["Total Founders / Key Profiles", stats["total_founders"]],
        ["Total Entity-Founder Links", stats["total_links"]],
        ["Database File", str(repo.db_path)],
        ["JSON Snapshot Export", str(settings.DATA_DIR / "wave1_candidates.json")],
    ]
    print("\n--- [1] DATABASE TOTALS ---")
    print(tabulate(overview_table, headers=["Metric", "Count / Value"], tablefmt="fancy_grid"))

    # 2. Source Breakdown Table
    source_table = [[src, count] for src, count in stats["signals_by_source"].items()]
    print("\n--- [2] SIGNALS INGESTED PER ADAPTER ---")
    print(tabulate(source_table, headers=["Adapter Source", "Signals Count"], tablefmt="fancy_grid"))

    # 3. Country Focus Breakdown (~70% DACH target)
    country_table = [[country, count] for country, count in sorted(stats["country_distribution"].items(), key=lambda x: -x[1])]
    print("\n--- [3] GEOGRAPHIC DISTRIBUTION (HTGF Target: ~70% DACH) ---")
    print(tabulate(country_table, headers=["Country Code", "Entities Count"], tablefmt="fancy_grid"))

    # 4. Sample Candidate Records
    print("\n--- [4] SAMPLE CANDIDATE RECORDS (TOP 5) ---")
    sample_rows = []
    for c in candidates[:5]:
        sample_rows.append([
            c["canonical_name"][:25],
            c.get("country_code") or "N/A",
            (c.get("normalized_domain") or c.get("website_url") or "N/A")[:30],
            len(c.get("signals", [])),
            len(c.get("founders", [])),
            (c.get("description") or "")[:50] + "..."
        ])
    print(tabulate(sample_rows, headers=["Name", "Country", "Domain / URL", "Signals", "Founders", "Description"], tablefmt="fancy_grid"))
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Wave 1 Ingestion Runner")
    parser.add_argument("--limit", type=int, default=30, help="Per-adapter candidate limit (default: 30)")
    args = parser.parse_args()

    asyncio.run(run_pipeline(limit_per_adapter=args.limit))


if __name__ == "__main__":
    main()
