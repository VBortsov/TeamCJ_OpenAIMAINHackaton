"""
Wave 2 Ingestion, Enrichment, Kill-Gates & ML Feature Pipeline Runner.

Orchestrates:
1. Funding Blocklist synchronization (RSS + YC <= 2024 + curated scaleups)
2. Ingestion across all 5 adapters (ETH/RWTH, OpenAlex, HF Hub, Luma Events, CORDIS ERC-PoC)
3. German § 5 DDG Impressum scraping & GitHub anti-fake-star verification
4. Deterministic 5-Gate Kill screening (Zero-LLM cost)
5. Top-K Founder Pedigree & HTGF LP Synergy enrichment (OpenAI + disk cache / heuristic fallback)
6. Transparent 0-100 multi-pillar lead scoring
7. ML Inference contract export (data/live_candidates_for_ml.csv)
8. Comprehensive diagnostic Funnel & Leaderboard summary tables
"""

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from tabulate import tabulate

from backend.config import settings, PLATFORM_DOMAINS
from backend.db.repository import DatabaseRepository
from backend.ingestion.base import BaseSignalAdapter, RawCandidate
from backend.ingestion.http_client import CachedHttpClient
from backend.ingestion.adapters import (
    EthRwthAdapter,
    OpenAlexAdapter,
    HuggingFaceHubAdapter,
    LumaEventsAdapter,
    CordisErcAdapter,
    FundingBlocklistAdapter,
)
from backend.enrichment.impressum import ImpressumScraper, ImpressumResult
from backend.enrichment.github_verifier import GitHubVerifier, GitHubRepoMetrics
from backend.scoring.kill_gates import KillGateEvaluator, KillGateResult
from backend.enrichment.openai_enricher import OpenAIEnricher, EnrichedProfile
from backend.scoring.scorer import LeadScorer, ScoreBreakdown
from backend.export.ml_exporter import export_ml_contract

# Configure readable logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("run_wave2")


async def run_wave2_pipeline(
    limit_per_adapter: int = 30,
    top_k: int = 25,
    skip_ingestion: bool = False
) -> DatabaseRepository:
    """Execute end-to-end Wave 2 pipeline."""
    start_time = time.time()
    logger.info("================================================================================")
    logger.info("Starting Wave 2 Pipeline: Ingestion + Impressum + Kill-Gates + Scorer + ML Export")
    logger.info("Target: MAIN Summit 2026 - HTGF Track (~70%% DACH Pre-Seed/Seed DeepTech)")
    logger.info("================================================================================")

    # 1. Initialize SQLite Database
    repo = DatabaseRepository(settings.SQLITE_DB_PATH)

    async with CachedHttpClient() as http_client:
        # =========================================================================
        # Stage 1: Synchronize Funding Blocklist
        # =========================================================================
        logger.info("\n--- [Stage 1] Synchronizing Funding Blocklist (Scaleups, YC <= 2024, Feeds) ---")
        blocklist_adapter = FundingBlocklistAdapter(repo.db_path, http_client=http_client)
        bl_count = await blocklist_adapter.sync_blocklist()
        logger.info("Funding Blocklist active: %d entities indexed", bl_count)

        # =========================================================================
        # Stage 2: Ingestion Across All 5 Signal Adapters
        # =========================================================================
        if not skip_ingestion:
            logger.info("\n--- [Stage 2] Running 5 Ingestion Adapters (Limit: %d each) ---", limit_per_adapter)
            adapters: List[BaseSignalAdapter] = [
                EthRwthAdapter(http_client=http_client),
                OpenAlexAdapter(http_client=http_client),
                HuggingFaceHubAdapter(http_client=http_client),
                LumaEventsAdapter(http_client=http_client),
                CordisErcAdapter(http_client=http_client),
            ]

            results = await asyncio.gather(
                *[adapter.fetch(limit=limit_per_adapter) for adapter in adapters],
                return_exceptions=True
            )

            for adapter, res in zip(adapters, results):
                if isinstance(res, Exception):
                    logger.error("Adapter '%s' failed: %s", adapter.name, res)
                    continue

                candidates: List[RawCandidate] = res
                logger.info("Persisting %d candidates from '%s'...", len(candidates), adapter.name)

                for cand in candidates:
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

                    for sig in cand.signals:
                        repo.upsert_signal(
                            entity_id=entity_id,
                            source_name=sig.source_name,
                            source_native_id=sig.source_native_id,
                            signal_date=sig.signal_date,
                            raw_metrics=sig.raw_metrics,
                            source_url=sig.source_url
                        )

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
        else:
            logger.info("Skipping Stage 2 ingestion (using existing database records).")

        # Load all ingested candidates from DB
        all_candidates = repo.export_all_candidates()
        total_raw_count = len(all_candidates)
        logger.info("Total entities in database for evaluation: %d", total_raw_count)

        # =========================================================================
        # Stage 3: German § 5 DDG Impressum Scraping & GitHub Verification
        # =========================================================================
        logger.info("\n--- [Stage 3] Impressum & Commercial Intent Scraping + GitHub Verifier ---")
        impressum_scraper = ImpressumScraper(http_client=http_client)
        github_verifier = GitHubVerifier(http_client=http_client)

        impressum_map: Dict[str, ImpressumResult] = {}
        github_map: Dict[str, GitHubRepoMetrics] = {}

        # Run Impressum scraping concurrently with concurrency limit (semaphore 5)
        sem = asyncio.Semaphore(5)

        async def scrape_impressum_task(cand: Dict[str, Any]):
            domain = cand.get("normalized_domain")
            if not domain or domain in PLATFORM_DOMAINS:
                return cand["entity_id"], None
            async with sem:
                res = await impressum_scraper.scrape(domain)
                return cand["entity_id"], res

        impressum_tasks = [scrape_impressum_task(c) for c in all_candidates]
        impressum_results = await asyncio.gather(*impressum_tasks, return_exceptions=True)

        for res in impressum_results:
            if isinstance(res, tuple) and res[1]:
                ent_id, imp_res = res
                impressum_map[ent_id] = imp_res
                # Update entity legal details in DB if found
                if imp_res.legal_name or imp_res.register_number or imp_res.legal_form:
                    repo.update_entity_impressum(
                        entity_id=ent_id,
                        legal_name=imp_res.legal_name,
                        legal_form=imp_res.legal_form,
                        register_number=imp_res.register_number,
                        country_code="DE" if imp_res.register_number and "HRB" in imp_res.register_number else None
                    )
                # Link newly discovered founders (Geschäftsführer)
                for f_name in imp_res.founders:
                    f_id = repo.upsert_founder(full_name=f_name)
                    repo.link_entity_founder(
                        entity_id=ent_id,
                        founder_id=f_id,
                        link_method="impressum_geschaeftsfuehrer",
                        link_confidence=0.95,
                        is_geschaeftsfuehrer=1
                    )

        logger.info("Completed Impressum scraping: %d entities enriched with corporate disclosures", len(impressum_map))

        # Run GitHub verification for candidates with GitHub signals or discovered orgs
        async def verify_github_task(cand: Dict[str, Any]):
            ent_id = cand["entity_id"]
            imp = impressum_map.get(ent_id)
            target = imp.github_org if imp and imp.github_org else None
            if not target:
                # Check founders
                for f in cand.get("founders", []):
                    if f.get("github_handle"):
                        target = f["github_handle"]
                        break
            if not target:
                # Check signals
                for s in cand.get("signals", []):
                    if "github.com" in s.get("source_url", ""):
                        target = s["source_url"]
                        break
            if target:
                async with sem:
                    gh_metrics = await github_verifier.verify(target)
                    return ent_id, gh_metrics
            return ent_id, None

        gh_tasks = [verify_github_task(c) for c in all_candidates]
        gh_results = await asyncio.gather(*gh_tasks, return_exceptions=True)
        for res in gh_results:
            if isinstance(res, tuple) and res[1]:
                ent_id, gh_met = res
                github_map[ent_id] = gh_met

        logger.info("Completed GitHub verification: %d repositories/orgs verified", len(github_map))

        # =========================================================================
        # Stage 4: Deterministic 5-Gate Kill Screening (Zero-LLM Cost)
        # =========================================================================
        logger.info("\n--- [Stage 4] Deterministic 5-Gate Kill Screening (Zero-LLM Cost) ---")
        evaluator = KillGateEvaluator(repo=repo)
        
        gate_counts = {
            "total_screened": 0,
            "gate1_hobby_failed": 0,
            "gate2_consultancy_failed": 0,
            "gate3_corporate_age_failed": 0,
            "gate4_fake_stars_failed": 0,
            "gate5_already_funded_failed": 0,
            "total_passed": 0,
        }

        # Reload updated candidates
        all_candidates = repo.export_all_candidates()

        for cand in all_candidates:
            ent_id = cand["entity_id"]
            imp = impressum_map.get(ent_id)
            gh = github_map.get(ent_id)

            stars = gh.stargazer_count if gh else 0
            fork_ratio = gh.fork_to_star_ratio if gh else 0.0

            result: KillGateResult = evaluator.evaluate(
                candidate=cand,
                impressum_legal_name=imp.legal_name if imp else None,
                impressum_text=imp.legal_form if imp else None,
                github_stars=stars,
                github_fork_ratio=fork_ratio
            )

            gate_counts["total_screened"] += 1
            if not result.gate1_passed:
                gate_counts["gate1_hobby_failed"] += 1
            if not result.gate2_passed:
                gate_counts["gate2_consultancy_failed"] += 1
            if not result.gate3_passed:
                gate_counts["gate3_corporate_age_failed"] += 1
            if not result.gate4_passed:
                gate_counts["gate4_fake_stars_failed"] += 1
            if not result.gate5_passed:
                gate_counts["gate5_already_funded_failed"] += 1

            if result.gate_passed:
                gate_counts["total_passed"] += 1

            repo.save_lead_score(
                entity_id=ent_id,
                gate_passed=result.gate_passed,
                kill_reasons=result.kill_reasons,
                deterministic_score=0.0
            )

        logger.info("Kill-Gates Screening Complete: %d / %d candidates passed (Pass rate: %.1f%%)",
                    gate_counts["total_passed"], gate_counts["total_screened"],
                    (gate_counts["total_passed"] / max(1, gate_counts["total_screened"])) * 100)

        # =========================================================================
        # Stage 5: Top-K Founder Pedigree Enrichment & Multi-Pillar Scoring
        # =========================================================================
        logger.info("\n--- [Stage 5] Pedigree Enrichment & Transparent 0-100 Multi-Pillar Scoring ---")
        enricher = OpenAIEnricher()

        # Fetch candidates that passed Kill-Gates
        surviving_candidates = repo.get_surviving_candidates()

        # Prioritize candidates: DACH / DE first, then spin-offs/academic signals, then signals count
        def priority_key(cand: Dict[str, Any]) -> tuple:
            cc = (cand.get("country_code") or "").upper()
            is_de = 1 if cc == "DE" else (0.85 if cc in ("CH", "AT") else 0.5)
            has_spinoff = 1 if any("spin-off" in t.lower() or "rwth" in t.lower() or "eth" in t.lower() or "erc" in t.lower() for t in cand.get("raw_tags", [])) else 0
            n_sigs = len(cand.get("signals", []))
            return (is_de, has_spinoff, n_sigs)

        surviving_candidates.sort(key=priority_key, reverse=True)
        top_candidates = surviving_candidates[:top_k]
        logger.info("Selected Top %d surviving leads for deep pedigree enrichment & scoring", len(top_candidates))

        scored_candidates: List[Dict[str, Any]] = []

        for cand in top_candidates:
            ent_id = cand["entity_id"]
            imp = impressum_map.get(ent_id)
            gh = github_map.get(ent_id)

            # 1. Enrich founder pedigree flags & HTGF LP matches (disk cache + OpenAI / heuristic fallback)
            profile: EnrichedProfile = enricher.enrich(cand)

            # 2. Compute 0-100 multi-pillar score
            score_breakdown: ScoreBreakdown = LeadScorer.calculate_score(
                candidate=cand,
                profile=profile,
                impressum=imp,
                github=gh
            )

            # 3. Update database with final score and breakdown
            repo.save_lead_score(
                entity_id=ent_id,
                gate_passed=True,
                kill_reasons=[],
                deterministic_score=score_breakdown.final_score,
                score_breakdown=score_breakdown.model_dump()
            )

            # Attach objects for ML export and summary
            cand["enriched_profile"] = profile
            cand["score_breakdown"] = score_breakdown
            cand["deterministic_score"] = score_breakdown.final_score
            scored_candidates.append(cand)

        # Sort scored candidates by final score descending
        scored_candidates.sort(key=lambda c: c["deterministic_score"], reverse=True)

        # =========================================================================
        # Stage 6: ML Inference Contract Export
        # =========================================================================
        logger.info("\n--- [Stage 6] Exporting ML Inference Contract & Clean Snapshots ---")
        ml_csv_path = settings.DATA_DIR / "live_candidates_for_ml.csv"
        export_ml_contract(scored_candidates, output_path=ml_csv_path)

        # Export wave2 candidates JSON snapshot
        json_export_path = settings.DATA_DIR / "wave2_candidates.json"
        all_exported = repo.export_all_candidates()
        with open(json_export_path, "w", encoding="utf-8") as f:
            json.dump(all_exported, f, indent=2, ensure_ascii=False)
        logger.info("Exported JSON snapshot to %s", json_export_path)

        # =========================================================================
        # Stage 7: Funnel Summary & Verification Reporting
        # =========================================================================
        duration = time.time() - start_time
        print_funnel_summary(
            total_raw=total_raw_count,
            gate_counts=gate_counts,
            top_k_count=len(scored_candidates),
            scored_candidates=scored_candidates,
            ml_csv_path=ml_csv_path,
            json_export_path=json_export_path,
            duration=duration
        )

    return repo


def print_funnel_summary(
    total_raw: int,
    gate_counts: Dict[str, int],
    top_k_count: int,
    scored_candidates: List[Dict[str, Any]],
    ml_csv_path: Path,
    json_export_path: Path,
    duration: float
) -> None:
    """Print readable verification tables using tabulate."""
    print("\n" + "=" * 90)
    print("                    WAVE 2 FUNNEL & VERIFICATION LEADERBOARD")
    print("=" * 90)

    # 1. Ingestion & Scoring Funnel Table
    funnel_rows = [
        ["1. Raw Ingested Candidates (5 Adapters)", total_raw, "100.0%"],
        ["2. Survived 5 Deterministic Kill-Gates", gate_counts["total_passed"], f"{(gate_counts['total_passed'] / max(1, total_raw)) * 100:.1f}%"],
        ["3. Deeply Enriched & Multi-Pillar Scored", top_k_count, f"{(top_k_count / max(1, total_raw)) * 100:.1f}%"],
        ["4. Top Ranked Leads for Partner Review", min(10, len(scored_candidates)), f"{(min(10, len(scored_candidates)) / max(1, total_raw)) * 100:.1f}%"],
    ]
    print("\n--- [1] INGESTION & SCREENING FUNNEL ---")
    print(tabulate(funnel_rows, headers=["Pipeline Stage", "Entities Count", "Conversion %"], tablefmt="fancy_grid"))

    # 2. Kill-Gate Casualties Breakdown Table
    casualties_rows = [
        ["Gate 1: Homework / Hobby / Community / Event Host", gate_counts["gate1_hobby_failed"]],
        ["Gate 2: Dev Agency / IT Consultancy (without IP)", gate_counts["gate2_consultancy_failed"]],
        ["Gate 3: Legacy Corporate / Big Tech / Age > 3.0 yrs", gate_counts["gate3_corporate_age_failed"]],
        ["Gate 4: StarScout Fake-Star Anomaly (fork_ratio < 0.03)", gate_counts["gate4_fake_stars_failed"]],
        ["Gate 5: Already Funded Seed+ / Series A+ / YC <= 2024", gate_counts["gate5_already_funded_failed"]],
        ["Total Passing All 5 Gates", gate_counts["total_passed"]],
    ]
    print("\n--- [2] DETERMINISTIC KILL-GATES SCREENING CASUALTIES ---")
    print(tabulate(casualties_rows, headers=["Screening Gate", "Casualties Flagged"], tablefmt="fancy_grid"))

    # 3. Top-10 Ranked Leads Leaderboard Table
    print("\n--- [3] TOP-10 RANKED EARLY-STAGE LEADS (HTGF TRACK) ---")
    top10_rows = []
    for rank, cand in enumerate(scored_candidates[:10], start=1):
        name = cand["canonical_name"][:22]
        cc = cand.get("country_code") or "N/A"
        score = cand["deterministic_score"]
        bd = cand["score_breakdown"]
        prof = cand["enriched_profile"]
        pedigree = f"{bd.team_pedigree:.0f}/32"
        intent = f"{bd.commercial_intent:.0f}/25"
        moat = f"{bd.technical_moat:.0f}/18"
        conv = f"{bd.multi_signal_convergence:.0f}/15"
        htgf = f"{bd.htgf_mandate_fit:.0f}/10"
        pre_cb = "Yes" if prof.is_pre_crunchbase else "No"
        lps = ", ".join(prof.lp_synergy_matches[:2])[:25]
        top10_rows.append([
            f"#{rank}", name, cc, f"{score:.1f}", pedigree, intent, moat, conv, htgf, pre_cb, lps
        ])

    headers = ["Rank", "Company", "Country", "Score", "Team", "Intent", "Moat", "Signals", "HTGF", "Pre-CB", "Top LP Synergies"]
    print(tabulate(top10_rows, headers=headers, tablefmt="fancy_grid"))

    # 4. Export Artifacts & Execution Stats
    print("\n--- [4] EXPORT ARTIFACTS & INTEGRATION FILES ---")
    export_rows = [
        ["ML Inference CSV Contract", str(ml_csv_path)],
        ["JSON Snapshot Export", str(json_export_path)],
        ["SQLite Database", str(settings.SQLITE_DB_PATH)],
        ["Total Wave 2 Execution Time", f"{duration:.2f} seconds"],
    ]
    print(tabulate(export_rows, headers=["Artifact / Metric", "Value"], tablefmt="fancy_grid"))
    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Wave 2 Ingestion, Enrichment & Scoring Runner")
    parser.add_argument("--limit", type=int, default=30, help="Per-adapter candidate limit (default: 30)")
    parser.add_argument("--top-k", type=int, default=25, help="Number of surviving leads to deeply enrich & score (default: 25)")
    parser.add_argument("--skip-ingestion", action="store_true", help="Skip re-ingestion and run pipeline on existing database")
    args = parser.parse_args()

    asyncio.run(run_wave2_pipeline(
        limit_per_adapter=args.limit,
        top_k=args.top_k,
        skip_ingestion=args.skip_ingestion
    ))


if __name__ == "__main__":
    main()
