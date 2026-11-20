"""
Wave 2 Ingestion, Enrichment, Kill-Gates, Scorer, ML Export & Frontend Provenance Pipeline.

Orchestrates:
1. Funding Blocklist synchronization (RSS + YC <= 2024 + curated scaleups)
2. Ingestion across all 5 adapters (ETH/RWTH, OpenAlex, HF Hub, Luma Events, CORDIS ERC-PoC)
3. German § 5 DDG Impressum scraping & GitHub anti-fake-star verification
4. Deterministic 5-Gate Kill screening (Zero-LLM cost)
5. Top-K Founder Pedigree & HTGF LP Synergy enrichment (OpenAI + disk cache / heuristic fallback)
6. Post-enrichment Company Age / Scaleup Kill-Gate enforcement (eliminates legacy corporates like Ingeneric)
7. Transparent 0-100 multi-pillar lead scoring with fine-grained differentiation
8. ML Inference contract export (data/live_candidates_for_ml.csv)
9. Frontend Provenance Feed export (data/wave2_frontend_feed.json) & Funnel Leaderboard
"""

from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
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
    YcRecentAdapter,
)
from backend.enrichment.impressum import ImpressumScraper, ImpressumResult
from backend.enrichment.github_verifier import GitHubVerifier, GitHubRepoMetrics
from backend.scoring.kill_gates import KillGateEvaluator, KillGateResult
from backend.enrichment.openai_enricher import OpenAIEnricher, EnrichedProfile
from backend.scoring.scorer import LeadScorer, ScoreBreakdown
from backend.export.ml_exporter import export_ml_contract
from backend.enrichment.cost_tracker import CostTracker
from backend.enrichment.import_founder_links import merge_offline_founder_profiles
from backend.db.repository import clean_url, is_valid_founder_linkedin, normalize_city

# Configure readable logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("run_wave2")


def format_source_label(sig: Dict[str, Any]) -> Dict[str, str]:
    """Transforms raw source signal into human-friendly provenance badge."""
    src = sig.get("source_name", "")
    url = sig.get("source_url", "")
    metrics = sig.get("raw_metrics", {})
    label = "Signal Feed"
    summary = "Discovered early-stage signal"

    if src == "eth_rwth":
        if "rwth" in url.lower() or "rwth" in str(metrics).lower():
            label = "RWTH Aachen 2026 Spin-off Award"
            summary = "Official winner of the RWTH Aachen University Spin-off Award"
        else:
            label = "ETH Zurich 2026 Spin-off Roster"
            summary = "Recognized academic spin-off from ETH Zurich"
    elif src == "cordis_erc":
        label = "EU CORDIS ERC Proof-of-Concept"
        summary = "Horizon Europe ERC Proof-of-Concept grant commercializing university research"
    elif src == "yc_recent":
        label = "Y Combinator Recent European Cohort"
        summary = "Fresh 2025-2026 YC European cohort startup"
    elif src == "hf_hub":
        label = "Hugging Face Trending AI Org"
        summary = "High developer velocity and model downloads on Hugging Face Hub"
    elif src == "openalex":
        label = "OpenAlex Research Co-Affiliation"
        summary = "Co-authorship between top European research lab and startup"
    elif src == "luma_events":
        label = "German AI Demo-Day & Pitch Event"
        summary = "Founder demo-day pitch at German tech hub event"

    return {
        "source_name": src,
        "source_label": label,
        "signal_date": sig.get("signal_date"),
        **({"event_title": (sig.get("raw_metrics") or {}).get("event_title"),
            "event_on": (sig.get("raw_metrics") or {}).get("event_start"),
            "event_city": (sig.get("raw_metrics") or {}).get("city")} if src == "luma_events" else {}),
        "source_url": url,
        "summary": summary
    }


async def run_wave2_pipeline(
    limit_per_adapter: int = 30,
    top_k: int = 200,
    skip_ingestion: bool = False,
    clear_cache: bool = False,
    confirm_clear_cache: bool = False,
    cache_only: bool = False,
    max_budget_usd: float = 2.00,
    refresh_events: bool = False
) -> DatabaseRepository:
    """Execute end-to-end Wave 2 / 2.5 pipeline."""
    start_time = time.time()
    logger.info("================================================================================")
    logger.info("Starting Wave 2 / 2.5 Pipeline: Ingestion + Provenance + Kill-Gates + Scorer")
    logger.info("Target: MAIN Summit 2026 - HTGF Track (~70%% DACH Pre-Seed/Seed DeepTech)")
    logger.info("================================================================================")

    # Initialize Cost & Budget Tracker
    cost_tracker = CostTracker(max_budget_usd=max_budget_usd, cache_only=cache_only)

    # Clear stale LLM cache ONLY if explicitly confirmed with safety lock
    llm_cache_dir = settings.DATA_DIR / "llm_cache"
    if clear_cache:
        if not confirm_clear_cache:
            logger.warning("SAFETY LOCK ACTIVE: --clear-cache was requested without --confirm-clear-cache! Cached data protected.")
        elif llm_cache_dir.exists():
            logger.warning("CONFIRMED CLEAR: Wiping LLM cache directory: %s", llm_cache_dir)
            shutil.rmtree(llm_cache_dir)
            llm_cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. Initialize SQLite Database
    repo = DatabaseRepository(settings.SQLITE_DB_PATH)

    # Merge optional offline founder profile enrichments if present
    founder_enrichment_file = settings.DATA_DIR / "founder_profiles_enrichment.json"
    if founder_enrichment_file.exists():
        logger.info("Discovered offline founder profiles at %s — merging...", founder_enrichment_file)
        merge_stats = merge_offline_founder_profiles(
            enrichment_file=founder_enrichment_file,
            cache_dir=settings.LLM_CACHE_DIR,
            db_path=settings.SQLITE_DB_PATH
        )
        logger.info("Offline founder profiles merged: %s", merge_stats)

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
            logger.info("\n--- [Stage 2] Running 6 Ingestion Adapters (Limit: %d each) ---", limit_per_adapter)
            adapters: List[BaseSignalAdapter] = [
                EthRwthAdapter(http_client=http_client),
                OpenAlexAdapter(http_client=http_client),
                HuggingFaceHubAdapter(http_client=http_client),
                LumaEventsAdapter(http_client=http_client),
                CordisErcAdapter(http_client=http_client),
                YcRecentAdapter(http_client=http_client),
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
                        city=cand.city,
                        accelerator=cand.accelerator,
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
                if imp_res.legal_name or imp_res.register_number or imp_res.legal_form or imp_res.city:
                    repo.update_entity_impressum(
                        entity_id=ent_id,
                        legal_name=imp_res.legal_name,
                        legal_form=imp_res.legal_form,
                        register_number=imp_res.register_number,
                        country_code="DE" if imp_res.register_number and "HRB" in imp_res.register_number else None,
                        city=imp_res.city
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
                for f in cand.get("founders", []):
                    if f.get("github_handle"):
                        target = f["github_handle"]
                        break
            if not target:
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
            "post_enrichment_age_kills": 0,
            "total_passed": 0,
        }

        # Reload updated candidates
        all_candidates = repo.export_all_candidates()
        rejected_audit: List[Dict[str, Any]] = []

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
            else:
                rejected_audit.append({
                    "entity_id": ent_id,
                    "canonical_name": cand["canonical_name"],
                    "country_code": cand.get("country_code") or "N/A",
                    "website_url": cand.get("website_url"),
                    "discovered_from": [format_source_label(s)["source_label"] for s in cand.get("signals", [])],
                    "kill_reasons": result.kill_reasons
                })

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
        # Stage 5: Equal Pedigree Enrichment Across ALL Surviving Leads (No Top-25 Bias!)
        # =========================================================================
        logger.info("\n--- [Stage 5] Equal Pedigree Enrichment & Multi-Pillar Scoring ---")
        enricher = OpenAIEnricher(cost_tracker=cost_tracker)

        # Fetch candidates that passed Kill-Gates
        surviving_candidates = repo.get_surviving_candidates()
        candidates_to_enrich = surviving_candidates[:top_k]
        logger.info("Found %d surviving leads across Kill-Gates for enrichment (top_k limit: %d)", len(surviving_candidates), top_k)

        # Tier-2 Real OpenAI Web Search enrichment with 6 parallel workers
        sem = asyncio.Semaphore(6)

        async def enrich_candidate_task(cand_dict: Dict[str, Any]) -> tuple[Dict[str, Any], EnrichedProfile]:
            async with sem:
                prof = await enricher.enrich_async(cand_dict)
                return cand_dict, prof

        logger.info("Running concurrent OpenAI Web Search enrichment (concurrency=6)...")
        enrichment_results = await asyncio.gather(*(enrich_candidate_task(c) for c in candidates_to_enrich))

        scored_candidates: List[Dict[str, Any]] = []

        for cand, profile in enrichment_results:
            ent_id = cand["entity_id"]
            imp = impressum_map.get(ent_id)
            gh = github_map.get(ent_id)

            # 2. Check for post-enrichment age or funding kills (e.g. Ingeneric, Menlo Systems founded <= 2022)
            if profile.is_post_enrichment_kill:
                logger.info("Rejecting '%s' post-enrichment: %s", cand["canonical_name"], profile.kill_reason)
                gate_counts["post_enrichment_age_kills"] += 1
                gate_counts["total_passed"] -= 1
                repo.save_lead_score(
                    entity_id=ent_id,
                    gate_passed=False,
                    kill_reasons=[profile.kill_reason or "Post-enrichment age/scaleup kill"],
                    deterministic_score=0.0
                )
                rejected_audit.append({
                    "entity_id": ent_id,
                    "name": cand["canonical_name"],
                    "canonical_name": cand["canonical_name"],
                    "country_code": cand.get("country_code") or "N/A",
                    "website_url": clean_url(cand.get("website_url")),
                    "discovered_from": [format_source_label(s)["source_label"] for s in cand.get("signals", [])],
                    "kill_reasons": [profile.kill_reason or "Gate 3: Company age > 3.5 years"]
                })
                continue

            # Update city on candidate if discovered
            if profile.city:
                cand["city"] = profile.city
                repo.update_entity_impressum(entity_id=ent_id, city=profile.city)

            # 3. Compute 0-100 multi-pillar score
            score_breakdown: ScoreBreakdown = LeadScorer.calculate_score(
                candidate=cand,
                profile=profile,
                impressum=imp,
                github=gh
            )

            # 4. Update database with final score and breakdown
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
        # Stage 6: ML Inference Contract & Frontend Provenance Feed Export
        # =========================================================================
        logger.info("\n--- [Stage 6] Exporting ML Inference Contract & Frontend Provenance Feed ---")
        ml_csv_path = settings.DATA_DIR / "live_candidates_for_ml.csv"
        export_ml_contract(scored_candidates, output_path=ml_csv_path)

        # Export wave2 candidates JSON snapshot
        json_export_path = settings.DATA_DIR / "wave2_candidates.json"
        all_exported = repo.export_all_candidates()
        with open(json_export_path, "w", encoding="utf-8") as f:
            json.dump(all_exported, f, indent=2, ensure_ascii=False)

        # Export Frontend Provenance Feed JSON (data/wave2_frontend_feed.json)
        frontend_feed_path = settings.DATA_DIR / "wave2_frontend_feed.json"
        feed_leads = []
        for cand in scored_candidates:
            prof: EnrichedProfile = cand["enriched_profile"]
            bd: ScoreBreakdown = cand["score_breakdown"]
            clean_lead_city = normalize_city(prof.city or cand.get("city") or "") or "Munich"
            clean_website = clean_url(cand.get("website_url"))

            # Real verified founders take priority over DB
            lead_founders = []
            if prof.founders_enriched:
                for f in prof.founders_enriched:
                    fd = f.model_dump()
                    cli = clean_url(fd.get("linkedin_url"))
                    fd["linkedin_url"] = cli if is_valid_founder_linkedin(cli) else None
                    lead_founders.append(fd)
            else:
                lead_founders = [
                    {
                        "full_name": f.get("full_name"),
                        "role": "Founder",
                        "linkedin_url": clean_url(f.get("linkedin_url")) if is_valid_founder_linkedin(f.get("linkedin_url")) else None,
                        "has_phd": prof.fd_any_phd,
                        "is_ex_bigtech": prof.fd_any_bigtech,
                        "prior_companies": [],
                        "universities": [clean_lead_city] if clean_lead_city else [],
                        "source_of_identity": f.get("link_method", "roster")
                    } for f in cand.get("founders", []) if not (f.get("full_name") or "").startswith("Lead PI (")
                ]

            feed_leads.append({
                "entity_id": cand["entity_id"],
                "name": cand["canonical_name"],
                "canonical_name": cand["canonical_name"],
                "website_url": clean_website,
                "country_code": cand.get("country_code") or "DE",
                "city": clean_lead_city,
                "legal_name": cand.get("legal_name") or cand["canonical_name"],
                "legal_form": cand.get("legal_form"),
                "register_number": cand.get("register_number"),
                "founding_year": prof.founding_year or 2025,
                "htgf_vertical": prof.htgf_vertical,
                "description": cand.get("description", ""),
                "discovered_at": cand.get("created_at"),
                "discovered_from": [format_source_label(s) for s in cand.get("signals", [])],
                "earliness_badge": "Pre-Crunchbase / Undiscovered" if prof.is_pre_crunchbase else "Grant-Only Pre-Seed",
                "funding_summary": prof.funding_summary,
                "score_breakdown": {
                    "final_score": bd.final_score,
                    "team_pedigree": bd.team_pedigree,
                    "commercial_intent": bd.commercial_intent,
                    "technical_moat": bd.technical_moat,
                    "multi_signal_convergence": bd.multi_signal_convergence,
                    "htgf_mandate_fit": bd.htgf_mandate_fit,
                    "drivers": bd.drivers,
                    "evidence_urls": [clean_url(u) for u in bd.evidence_urls if u]
                },
                "founders": lead_founders,
                "inflection_why_now": prof.inflection_why_now,
                "devils_advocate_risks": prof.devils_advocate_risks,
                "lp_synergy_matches": [lp.model_dump() for lp in prof.lp_synergy_matches]
            })

        dach_count = sum(1 for c in scored_candidates if (c.get("country_code") or "").upper() in ("DE", "CH", "AT"))
        frontend_feed = {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_raw": total_raw_count,
                "passed_kill_gates": len(scored_candidates),
                "enriched_count": len(scored_candidates),
                "germany_dach_share_pct": round((dach_count / max(1, len(scored_candidates))) * 100, 1)
            },
            "funnel": {
                "raw_ingested": total_raw_count,
                "gate1_hobby_failed": gate_counts["gate1_hobby_failed"],
                "gate2_consultancy_failed": gate_counts["gate2_consultancy_failed"],
                "gate3_corporate_age_failed": gate_counts["gate3_corporate_age_failed"],
                "gate4_fake_stars_failed": gate_counts["gate4_fake_stars_failed"],
                "gate5_already_funded_failed": gate_counts["gate5_already_funded_failed"],
                "post_enrichment_age_kills": gate_counts["post_enrichment_age_kills"],
                "survived_all_gates": len(scored_candidates)
            },
            "ranked_leads": feed_leads,
            "rejected_audit": rejected_audit
        }

        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=frontend_feed_path.parent, delete=False) as output:
            json.dump(frontend_feed, output, indent=2, ensure_ascii=False)
        Path(output.name).replace(frontend_feed_path)
        logger.info("Exported Frontend Provenance Feed to %s", frontend_feed_path)

        # Export European Events Sourcing Catalog
        events_feed_path = settings.DATA_DIR / "events_feed.json"
        if refresh_events or not events_feed_path.exists():
            logger.info("Generating European events sourcing catalog from 12 hubs...")
            luma_adapter = LumaEventsAdapter(http_client=http_client)
            catalog = await luma_adapter.fetch_events_catalog(limit_per_city=15)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=events_feed_path.parent, delete=False) as output:
                json.dump(catalog, output, indent=2, ensure_ascii=False)
            Path(output.name).replace(events_feed_path)
            logger.info("Persisted %d European sourcing events to %s", len(catalog), events_feed_path)

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
            frontend_feed_path=frontend_feed_path,
            events_feed_path=events_feed_path,
            duration=duration,
            cost_tracker=cost_tracker
        )

    return repo


def print_funnel_summary(
    total_raw: int,
    gate_counts: Dict[str, int],
    top_k_count: int,
    scored_candidates: List[Dict[str, Any]],
    ml_csv_path: Path,
    json_export_path: Path,
    frontend_feed_path: Path,
    duration: float,
    cost_tracker: Optional[CostTracker] = None,
    events_feed_path: Optional[Path] = None
) -> None:
    """Print readable verification tables using tabulate."""
    print("\n" + "=" * 90)
    print("               WAVE 2.5 / 3 FUNNEL & VERIFICATION LEADERBOARD")
    print("=" * 90)

    # 1. Ingestion & Scoring Funnel Table
    funnel_rows = [
        ["1. Raw Ingested Candidates (5 Adapters)", total_raw, "100.0%"],
        ["2. Survived Initial 5 Kill-Gates", gate_counts["total_passed"] + gate_counts["post_enrichment_age_kills"], f"{((gate_counts['total_passed'] + gate_counts['post_enrichment_age_kills']) / max(1, total_raw)) * 100:.1f}%"],
        ["3. Deeply Enriched & Scored (Post-Age Kills)", top_k_count, f"{(top_k_count / max(1, total_raw)) * 100:.1f}%"],
        ["4. Top Ranked Leads for Partner Review", min(10, len(scored_candidates)), f"{(min(10, len(scored_candidates)) / max(1, total_raw)) * 100:.1f}%"],
    ]
    print("\n--- [1] INGESTION & SCREENING FUNNEL ---")
    print(tabulate(funnel_rows, headers=["Pipeline Stage", "Entities Count", "Conversion %"], tablefmt="fancy_grid"))

    # 2. Kill-Gate Casualties Breakdown Table
    casualties_rows = [
        ["Gate 1: Homework / Hobby / Community / Event Host", gate_counts["gate1_hobby_failed"]],
        ["Gate 2: Dev Agency / IT Consultancy (without IP)", gate_counts["gate2_consultancy_failed"]],
        ["Gate 3: Legacy Corporate / Big Tech / Age > 3.5 yrs (Initial)", gate_counts["gate3_corporate_age_failed"]],
        ["Gate 4: StarScout Fake-Star Anomaly (fork_ratio < 0.03)", gate_counts["gate4_fake_stars_failed"]],
        ["Gate 5: Already Funded Seed+ / Series A+ / YC <= 2024", gate_counts["gate5_already_funded_failed"]],
        ["Post-Enrichment Kill: Company Age > 3.5 yrs (e.g. Ingeneric)", gate_counts["post_enrichment_age_kills"]],
        ["Total Passing All Gates (Pre-Crunchbase Leads)", top_k_count],
    ]
    print("\n--- [2] DETERMINISTIC KILL-GATES SCREENING CASUALTIES ---")
    print(tabulate(casualties_rows, headers=["Screening Gate", "Casualties Flagged"], tablefmt="fancy_grid"))

    # 3. Top-10 Ranked Leads Leaderboard Table
    print("\n--- [3] TOP-10 RANKED EARLY-STAGE LEADS (HTGF TRACK) ---")
    top10_rows = []
    for rank, cand in enumerate(scored_candidates[:10], start=1):
        name = cand["canonical_name"][:20]
        cc = cand.get("country_code") or "N/A"
        city = (cand.get("city") or cand.get("enriched_profile").city or "N/A")[:12]
        score = cand["deterministic_score"]
        bd = cand["score_breakdown"]
        prof = cand["enriched_profile"]
        pedigree = f"{bd.team_pedigree:.0f}/32"
        intent = f"{bd.commercial_intent:.0f}/25"
        moat = f"{bd.technical_moat:.0f}/18"
        conv = f"{bd.multi_signal_convergence:.0f}/15"
        htgf = f"{bd.htgf_mandate_fit:.0f}/10"
        pre_cb = "Yes" if prof.is_pre_crunchbase else "No"
        lps = ", ".join([lp.lp_name for lp in prof.lp_synergy_matches[:2]])[:24]
        top10_rows.append([
            f"#{rank}", name, cc, city, f"{score:.1f}", pedigree, intent, moat, conv, htgf, pre_cb, lps
        ])

    headers = ["Rank", "Company", "Country", "City", "Score", "Team", "Intent", "Moat", "Signals", "HTGF", "Pre-CB", "Top LP Synergies"]
    print(tabulate(top10_rows, headers=headers, tablefmt="fancy_grid"))

    # 4. Export Artifacts & Execution Stats
    print("\n--- [4] EXPORT ARTIFACTS & INTEGRATION FILES ---")
    export_rows = [
        ["ML Inference CSV Contract", str(ml_csv_path)],
        ["Frontend Provenance Feed JSON", str(frontend_feed_path)],
        *( [["European Events Feed JSON", str(events_feed_path)]] if events_feed_path and events_feed_path.exists() else [] ),
        ["JSON Snapshot Export", str(json_export_path)],
        ["SQLite Database", str(settings.SQLITE_DB_PATH)],
        ["Total Pipeline Execution Time", f"{duration:.2f} seconds"],
    ]
    print(tabulate(export_rows, headers=["Artifact / Metric", "Value"], tablefmt="fancy_grid"))

    # 5. API Cost & Cache Report
    print("\n--- [5] API COST & CACHE REPORT ---")
    tracker = cost_tracker or CostTracker()
    cost_summary = tracker.get_summary()
    cost_rows = [
        ["Cache Hits ($0.00)", str(cost_summary["cache_hits"])],
        ["Live OpenAI Calls", str(cost_summary["live_llm_calls"])],
        ["Web Search Calls", str(cost_summary["web_search_calls"])],
        ["Token Usage (Input / Output)", f"{cost_summary['input_tokens']:,} / {cost_summary['output_tokens']:,}"],
        ["Estimated Run Cost (USD)", f"${cost_summary['estimated_run_cost_usd']:.4f}"],
        ["Cumulative Ledger Spend (USD)", f"${cost_summary['cumulative_ledger_spend_usd']:.4f}"],
        ["Circuit-Breaker Budget Cap", f"${cost_summary['max_budget_usd']:.2f}"],
        ["Cache-Only Mode", "ACTIVE" if cost_summary["cache_only"] else "INACTIVE"],
    ]
    print(tabulate(cost_rows, headers=["Metric", "Value"], tablefmt="fancy_grid"))
    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Wave 2.5 / 3 Pipeline Runner")
    parser.add_argument("--limit", type=int, default=30, help="Per-adapter candidate limit (default: 30)")
    parser.add_argument("--top-k", type=int, default=200, help="Number of surviving leads to deeply enrich & score (default: 200)")
    parser.add_argument("--skip-ingestion", action="store_true", help="Skip re-ingestion and run pipeline on existing database")
    parser.add_argument("--cache-only", action="store_true", help="Strictly forbid live OpenAI calls; only read from data/llm_cache/ ($0.00)")
    parser.add_argument("--max-budget-usd", type=float, default=2.00, help="Hard circuit-breaker budget cap in USD (default: 2.00)")
    parser.add_argument("--clear-cache", action="store_true", help="Clear stale data/llm_cache before running (requires --confirm-clear-cache)")
    parser.add_argument("--confirm-clear-cache", action="store_true", help="Confirm clearing cache (safety protection)")
    parser.add_argument("--refresh-events", action="store_true", help="Fetch fresh European sourcing events catalog from Luma into data/events_feed.json")
    args = parser.parse_args()

    asyncio.run(run_wave2_pipeline(
        limit_per_adapter=args.limit,
        top_k=args.top_k,
        skip_ingestion=args.skip_ingestion,
        clear_cache=args.clear_cache,
        confirm_clear_cache=args.confirm_clear_cache,
        cache_only=args.cache_only,
        max_budget_usd=args.max_budget_usd,
        refresh_events=args.refresh_events
    ))


if __name__ == "__main__":
    main()
