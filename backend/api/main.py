"""
FastAPI Backend Server for MAIN Summit 2026 - HTGF Sourcing Engine.
Serves:
- Dashboard provenance feed (instant cached load for demo safety)
- Filterable ranked early-stage leads
- Detailed 1-page Investment Memos & Dossiers
- Founder GitHub technical profile inspection
- Rejected noise audit trail
- On-demand live Impressum + web-search enrichment & scoring
- N8N webhook dispatch / preview
"""

from __future__ import annotations
import asyncio
import csv
import hashlib
import hmac
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import settings
from backend.db.repository import (
    DatabaseRepository,
    clean_url,
    is_valid_founder_linkedin
)
from backend.enrichment.cost_tracker import CostTracker
from backend.enrichment.impressum import ImpressumResult
from backend.enrichment.github_verifier import GitHubVerifier, FounderGitHubProfile
from backend.scoring.kill_gates import KillGateEvaluator
from backend.enrichment.openai_enricher import OpenAIEnricher
from backend.scoring.scorer import LeadScorer, ScoreBreakdown
from backend.ml import service as ml_service
from backend.ml.memo_agent import build_memo_input, generate_memo, load_cached_memo

logger = logging.getLogger("api")

tags_metadata = [
    {
        "name": "System",
        "description": "System health, database metrics, and API cost & cache monitoring ($0.00 safety monitor).",
    },
    {
        "name": "Dashboard",
        "description": "Full provenance feed, conversion funnel, ranked leads, and filtered noise audit for UI widgets.",
    },
    {
        "name": "Leads",
        "description": "Searchable, filterable early-stage DeepTech leads and 1-page Investment Memos for partner review.",
    },
    {
        "name": "Founders",
        "description": "Founder technical track record verification via live GitHub profile inspection.",
    },
    {
        "name": "Enrichment",
        "description": "On-demand live Impressum scraping, Kill-Gate screening, and web search enrichment for new domains.",
    },
    {
        "name": "ML",
        "description": "ML ranking model (ml_score 0-100) and fact-grounded LLM investment memos for the top leads.",
    },
    {
        "name": "Integrations",
        "description": "Outbound webhook dispatch to n8n for CRM / Lusha contact enrichment and Slack notifications.",
    },
]

app = FastAPI(
    title="HTGF Early-Stage European Sourcing Engine API",
    description=(
        "High-precision early-stage European startup sourcing engine for High-Tech Gründerfonds (HTGF) "
        "track at MAIN Summit 2026 (~70% DACH focus, Pre-Seed & Seed DeepTech, AI, Industrial, Life Sciences). "
        "Combines multi-source signal ingestion, 5 deterministic Kill-Gates, Impressum compliance extraction, "
        "live GitHub technical verification, async OpenAI web search pedigree enrichment, and optional "
        "ML scoring blend with complete auditable provenance."
    ),
    version="2.5.0",
    openapi_tags=tags_metadata
)

# Same-origin hosting works without CORS. Separate frontend deployments opt in by origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FOUNDERHUNT_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

FEED_PATH = settings.DATA_DIR / "wave2_frontend_feed.json"
ML_PRED_PATH = settings.DATA_DIR / "ml_predictions.csv"
LIVE_LEADS_DIR = settings.DATA_DIR / "live_leads"
SAVED_WORKSPACE_PATH = settings.DATA_DIR / "saved_workspace.json"
live_enrichment_lock = asyncio.Lock()


def require_operator(authorization: str | None) -> None:
    token = settings.FOUNDERHUNT_WRITE_TOKEN
    if not token:
        raise HTTPException(status_code=503, detail="The operator token is not configured.")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(status_code=401, detail="A valid operator token is required.")


def write_json_atomically(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output, ensure_ascii=False)
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def store_live_lead(lead: Dict[str, Any]) -> None:
    write_json_atomically(LIVE_LEADS_DIR / f"{lead['entity_id']}.json", lead)


def get_repo() -> DatabaseRepository:
    return DatabaseRepository(settings.SQLITE_DB_PATH)


def load_ml_predictions() -> Dict[str, float]:
    """Reads optional ML predictions CSV (columns: company_id, ml_score) if present."""
    if not ML_PRED_PATH.exists():
        return {}
    preds: Dict[str, float] = {}
    try:
        with open(ML_PRED_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = (row.get("company_id") or row.get("entity_id") or "").strip()
                score_str = (row.get("ml_score") or row.get("score") or "").strip()
                if cid and score_str:
                    try:
                        preds[cid] = float(score_str)
                    except ValueError:
                        continue
    except Exception as e:
        logger.warning("Could not read ml_predictions.csv: %s", e)
    return preds


def load_frontend_feed() -> Dict[str, Any]:
    """
    Loads frontend provenance feed. If data/ml_predictions.csv exists,
    automatically attaches ml_score (0-100) and blended_score (0.5 * det + 0.5 * ml)
    and sorts leads by blended_score descending.
    """
    if not FEED_PATH.exists():
        feed = {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_raw": 0,
                "passed_kill_gates": 0,
                "enriched_count": 0,
                "germany_dach_share_pct": 0.0
            },
            "funnel": {},
            "ranked_leads": [],
            "rejected_audit": []
        }
    else:
        with open(FEED_PATH, "r", encoding="utf-8") as f:
            feed = json.load(f)

    # Attach ML scores: data/ml_predictions.csv is (re)built automatically from the exported leads
    ml_service.ensure_predictions()
    ml_preds = load_ml_predictions()
    leads = feed.setdefault("ranked_leads", [])
    existing_ids = {lead.get("entity_id") for lead in leads}
    for path in sorted(LIVE_LEADS_DIR.glob("live-*.json")):
        try:
            lead = json.loads(path.read_text(encoding="utf-8"))
            if lead["entity_id"] not in existing_ids:
                leads.append(lead)
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read live lead %s", path.name)
    for lead in leads:
        cid = lead.get("entity_id")
        det_score = lead.get("score_breakdown", {}).get("final_score") or lead.get("deterministic_score", 0.0)
        if ml_preds and cid in ml_preds:
            ml_s = round(float(ml_preds[cid]), 1)
            blended = round(0.5 * det_score + 0.5 * ml_s, 1)
            lead["ml_score"] = ml_s
            lead["blended_score"] = blended
        else:
            lead["ml_score"] = None
            lead["blended_score"] = det_score

    if ml_preds or any(lead.get("discovered_at") for lead in leads):
        feed["ranked_leads"] = sorted(leads, key=lambda l: l.get("blended_score", 0.0), reverse=True)

    return feed


class LiveEnrichRequest(BaseModel):
    domain_or_name: str = Field(min_length=1, max_length=180, description="Public startup website or company name")


class SavedWorkspace(BaseModel):
    decisions: Dict[str, str] = Field(default_factory=dict, max_length=2000)
    tracked: Dict[str, str] = Field(default_factory=dict, max_length=2000)


@app.get("/api/workspace/saved", tags=["Integrations"])
def read_saved_workspace(authorization: str | None = Header(None)):
    require_operator(authorization)
    if not SAVED_WORKSPACE_PATH.exists():
        return SavedWorkspace()
    return SavedWorkspace.model_validate_json(SAVED_WORKSPACE_PATH.read_text(encoding="utf-8"))


@app.post("/api/workspace/saved", tags=["Integrations"])
def save_workspace(body: SavedWorkspace, authorization: str | None = Header(None)):
    require_operator(authorization)
    if any(not re.fullmatch(r"[\w:-]{1,160}", item) for item in [*body.decisions, *body.tracked]):
        raise HTTPException(status_code=400, detail="Invalid saved item identifier.")
    if any(value not in {"shortlist", "watch", "pass"} for value in body.decisions.values()) or any(
        value != "track" for value in body.tracked.values()
    ):
        raise HTTPException(status_code=400, detail="Invalid saved item state.")
    write_json_atomically(SAVED_WORKSPACE_PATH, body.model_dump())
    return body


# =============================================================================
# 1. Health & Dashboard Endpoints
# =============================================================================
@app.get(
    "/api/health",
    tags=["System"],
    summary="System Health & API Spend Monitor",
    description="Returns database entity counts, cached leads inventory, and cumulative API spend from cost ledger."
)
def health_check():
    repo = get_repo()
    stats = repo.get_stats()
    tracker = CostTracker(max_budget_usd=settings.FOUNDERHUNT_ENRICH_BUDGET_USD)
    cache_dir = settings.DATA_DIR / "llm_cache"
    cached_leads = len(list(cache_dir.glob("*.json"))) if cache_dir.exists() else 0
    return {
        "status": "healthy",
        "version": "2.5.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {
            "path": str(repo.db_path),
            "total_entities": stats.get("total_entities", 0),
            "total_signals": stats.get("total_signals", 0)
        },
        "cost_and_cache": {
            "cached_leads_count": cached_leads,
            "cumulative_spend_usd": tracker.get_cumulative_ledger_spend(),
            "max_budget_cap_usd": tracker.max_budget_usd
        }
    }


@app.get(
    "/api/dashboard",
    tags=["Dashboard"],
    summary="Complete Provenance Feed & Funnel",
    description="Returns full provenance feed: conversion funnel, all ranked leads with scores, and rejected noise audit."
)
def get_dashboard():
    return load_frontend_feed()


@app.get("/api/events", tags=["Dashboard"], summary="Public event signals linked to ranked ventures")
def list_events():
    events: Dict[str, Any] = {}
    for lead in load_frontend_feed().get("ranked_leads", []):
        for signal in lead.get("discovered_from", []):
            if signal.get("source_name") != "luma_events":
                continue
            url = clean_url(signal.get("source_url"))
            date = signal.get("event_on") or signal.get("signal_date")
            title = signal.get("event_title")
            if not (url and date and title):
                continue
            event = events.setdefault(url, {
                "name": title, "date": date, "url": url, "location": signal.get("event_city"),
                "projects": []
            })
            event["projects"].append([lead["entity_id"], "Named as an organiser in the source event listing."])
    return {"events": sorted(events.values(), key=lambda event: event["date"])}


# =============================================================================
# 2. Leads & Investment Memo Endpoints
# =============================================================================
@app.get(
    "/api/leads",
    tags=["Leads"],
    summary="Filterable Ranked Leads",
    description="Filter top early-stage DeepTech leads by HTGF vertical, country code, or minimum score threshold."
)
def list_leads(
    vertical: Optional[str] = Query(None, description="Filter by HTGF vertical: industrial_climate_deeptech, digital_tech_ai, life_sciences_chemistry"),
    country: Optional[str] = Query(None, description="Filter by 2-letter country code (DE, CH, AT, etc.)"),
    min_score: float = Query(0.0, ge=0.0, le=100.0, description="Minimum final score threshold")
):
    feed = load_frontend_feed()
    leads = feed.get("ranked_leads", [])

    filtered = []
    for lead in leads:
        if vertical and lead.get("htgf_vertical") != vertical:
            continue
        if country and (lead.get("country_code") or "").upper() != country.upper():
            continue
        score = lead.get("blended_score") if lead.get("blended_score") is not None else lead.get("score_breakdown", {}).get("final_score", 0.0)
        if score < min_score:
            continue
        filtered.append(lead)

    return {
        "total_matching": len(filtered),
        "leads": filtered
    }


@app.get(
    "/api/leads/{entity_id}",
    tags=["Leads"],
    summary="1-Page Investment Memo & Partner Dossier",
    description="Generates an auditable 1-page Investment Memo with thesis, founder pedigree, IP moat, LP synergy, and devil's advocate risks."
)
def get_lead_dossier(entity_id: str):
    feed = load_frontend_feed()
    leads = feed.get("ranked_leads", [])

    matched = next((l for l in leads if l.get("entity_id") == entity_id), None)
    if not matched:
        # Fallback to database lookup
        repo = get_repo()
        all_cands = repo.export_all_candidates()
        db_cand = next((c for c in all_cands if c.get("entity_id") == entity_id), None)
        if not db_cand:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
        matched = db_cand

    # Structure 1-page Investment Memo
    overall_score = matched.get("blended_score") if matched.get("blended_score") is not None else matched.get("score_breakdown", {}).get("final_score", 0.0)

    memo = {
        "entity_id": matched.get("entity_id"),
        "name": matched.get("name") or matched.get("canonical_name"),
        "canonical_name": matched.get("canonical_name") or matched.get("name"),
        "legal_name": matched.get("legal_name") or matched.get("canonical_name"),
        "website_url": matched.get("website_url"),
        "country_code": matched.get("country_code"),
        "city": matched.get("city"),
        "founding_year": matched.get("founding_year"),
        "htgf_vertical": matched.get("htgf_vertical", "industrial_climate_deeptech"),
        "earliness_badge": matched.get("earliness_badge", "Pre-Crunchbase / Undiscovered"),
        
        # Scoring metrics
        "overall_score": overall_score,
        "deterministic_score": matched.get("score_breakdown", {}).get("final_score", 0.0),
        "ml_score": matched.get("ml_score"),
        "blended_score": matched.get("blended_score"),

        # Section 1: Executive Summary & Thesis
        "executive_summary": matched.get("description"),
        "investment_thesis": {
            "why_now": matched.get("inflection_why_now"),
            "catalyst": matched.get("catalyst")
        },

        # Section 2: Founder Pedigree & Technical Team
        "team_dossier": {
            "founders": matched.get("founders", []),
            "has_phd": any(f.get("has_phd") for f in matched.get("founders", [])),
            "university_origin": matched.get("university_origin"),
            "pedigree_score": matched.get("score_breakdown", {}).get("team_pedigree", 0.0)
        },

        # Section 3: Technology & IP Moat
        "technology_moat": {
            "spin_off_origin": matched.get("discovered_from", []),
            "moat_score": matched.get("score_breakdown", {}).get("technical_moat", 0.0),
            "ip_status": matched.get("ip_status")
        },

        # Section 4: Commercial Traction & Impressum Compliance
        "commercial_traction": {
            "register_number": matched.get("register_number"),
            "commercial_score": matched.get("score_breakdown", {}).get("commercial_intent", 0.0),
            "status": matched.get("funding_summary")
        },

        # Section 5: HTGF IV Industrial LP Synergy
        "lp_synergy_analysis": matched.get("lp_synergy_matches", []),

        # Section 6: Devil's Advocate Risks & Mitigations
        "devils_advocate_risks": matched.get("devils_advocate_risks", []),

        # Section 7: Audit Trail & Clickable Evidence
        "evidence_urls": matched.get("score_breakdown", {}).get("evidence_urls", []),
        "score_drivers": matched.get("score_breakdown", {}).get("drivers", []),

        # Section 8: ML memo (null until generated via POST /api/ml/score or GET /api/leads/{id}/ml-memo)
        "ml_memo": load_cached_memo(entity_id)
    }
    return memo


# =============================================================================
# 3. Founder Technical Profile Verification Endpoint
# =============================================================================
@app.get(
    "/api/founders/github/{username}",
    tags=["Founders"],
    summary="Founder GitHub Technical Track Record",
    description="Inspects verified public GitHub profile, repository stats, stars, forks, languages, and social links using GitHub token."
)
async def get_founder_github_profile(username: str):
    verifier = GitHubVerifier()
    profile = await verifier.verify_founder(username)
    if not profile:
        raise HTTPException(status_code=404, detail=f"GitHub user '{username}' not found")
    return profile.model_dump()


# =============================================================================
# 4. Noise / Filtered Out Audit Endpoint
# =============================================================================
@app.get(
    "/api/rejected",
    tags=["Dashboard"],
    summary="Filtered Out Noise Audit Trail",
    description="Returns noise filtered out by Gates 1-5 (consultancies, agencies, fake stars, scaleups) proving sourcing rigor."
)
def list_rejected_entities():
    feed = load_frontend_feed()
    rejected = feed.get("rejected_audit", [])
    return {
        "total_rejected": len(rejected),
        "rejected_entities": rejected
    }


# =============================================================================
# 5. Live On-Demand Enrichment Endpoint
# =============================================================================
@app.post("/api/enrich/live", tags=["Enrichment"], summary="Evaluate a startup using public web sources")
async def live_enrich_startup(req: LiveEnrichRequest, authorization: str | None = Header(None)):
    require_operator(authorization)
    target = req.domain_or_name.strip()
    if not target or any(ord(char) < 32 for char in target):
        raise HTTPException(status_code=400, detail="Enter a startup name or public website.")

    website = None
    if "://" in target or ("." in target and not any(char.isspace() for char in target)):
        try:
            parsed = urlsplit(target if "://" in target else "https://" + target)
            domain = parsed.hostname or ""
            valid = (parsed.scheme in ("http", "https") and not parsed.username and not parsed.port and
                     re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", domain))
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(status_code=400, detail="Enter a startup name or public website.")
        target = domain.removeprefix("www.")
        website = f"https://{domain}"

    entity_id = "live-" + hashlib.sha256(target.casefold().encode()).hexdigest()[:24]
    candidate = {
        "entity_id": entity_id, "canonical_name": target, "website_url": website,
        "raw_tags": [], "description": "", "founders": [], "signals": []
    }
    # Submitted websites are research context. The API does not fetch arbitrary submitted URLs.
    async with live_enrichment_lock:
        tracker = CostTracker(max_budget_usd=settings.FOUNDERHUNT_ENRICH_BUDGET_USD)
        profile = await OpenAIEnricher(cost_tracker=tracker).enrich_async(candidate)
        if not profile.fd_enriched:
            raise HTTPException(status_code=503, detail="Live research is unavailable. Check the provider configuration and enrichment budget.")
        citations = list(dict.fromkeys(url for raw in profile.citations if (url := clean_url(raw))))
        if not citations:
            raise HTTPException(status_code=422, detail="No source-backed evaluation was found for this startup.")
        candidate["canonical_name"] = profile.canonical_name or target
        candidate["description"] = profile.description
        screened = KillGateEvaluator(repo=get_repo()).evaluate(candidate=candidate)
        reasons = [*screened.kill_reasons]
        if profile.is_post_enrichment_kill:
            reasons.append(profile.kill_reason or "The venture did not meet the enrichment criteria.")
        breakdown = LeadScorer.calculate_score(candidate=candidate, profile=profile, impressum=ImpressumResult())
        lead = {
            "entity_id": entity_id, "name": profile.canonical_name or target, "canonical_name": profile.canonical_name or target,
            "domain": target if website else None, "website_url": website,
            "city": profile.city, "founding_year": profile.founding_year,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "htgf_vertical": profile.htgf_vertical, "description": profile.description,
            "inflection_why_now": profile.inflection_why_now, "funding_summary": profile.funding_summary,
            "founders": [founder.model_dump() for founder in profile.founders_enriched],
            "lp_synergy_matches": [match.model_dump() for match in profile.lp_synergy_matches],
            "devils_advocate_risks": profile.devils_advocate_risks,
            "discovered_from": [{"source_label": "Public web source", "source_url": url} for url in citations],
            "score_breakdown": {**breakdown.model_dump(), "evidence_urls": citations},
            "gate_passed": not reasons, "kill_reasons": reasons,
            "final_score": breakdown.final_score if not reasons else 0.0,
            "founders_enriched": [founder.model_dump() for founder in profile.founders_enriched]
        }
        if not reasons:
            previous = LIVE_LEADS_DIR / f"{entity_id}.json"
            if previous.is_file():
                try:
                    lead["discovered_at"] = json.loads(previous.read_text(encoding="utf-8")).get("discovered_at") or lead["discovered_at"]
                except (OSError, ValueError):
                    logger.warning("Could not recover the discovery date for %s", entity_id)
            store_live_lead(lead)
        return lead


# =============================================================================
# 6. N8N Webhook Dispatch Endpoint
# =============================================================================
@app.post(
    "/api/dispatch/n8n/{entity_id}",
    tags=["Integrations"],
    summary="Dispatch Lead to n8n Webhook",
    description="Dispatches top lead payload to n8n for Lusha contact enrichment and Slack notifications. Returns simulation if webhook not configured."
)
async def dispatch_lead_to_n8n(entity_id: str, authorization: str | None = Header(None)):
    webhook_url = settings.N8N_WEBHOOK_URL
    if webhook_url:
        require_operator(authorization)
    memo = get_lead_dossier(entity_id)

    payload = {
        "event": "htgf_top_lead_identified",
        "dispatched_at": datetime.now(timezone.utc).isoformat(),
        "lead_id": memo["entity_id"],
        "company_name": memo["canonical_name"],
        "website_url": memo["website_url"],
        "country": memo["country_code"],
        "city": memo["city"],
        "score": memo["overall_score"],
        "vertical": memo["htgf_vertical"],
        "earliness_badge": memo["earliness_badge"],
        "why_now": memo["investment_thesis"]["why_now"],
        "founders_to_enrich": [
            {
                "name": f.get("full_name"),
                "role": f.get("role", "Founder"),
                "linkedin": clean_url(f.get("linkedin_url")) if is_valid_founder_linkedin(f.get("linkedin_url")) else None
            } for f in memo.get("team_dossier", {}).get("founders", []) if not (f.get("full_name") or "").startswith("Lead PI (")
        ],
        "lp_synergies": [lp.get("lp_name") for lp in memo.get("lp_synergy_analysis", [])],
        "key_drivers": memo["score_drivers"]
    }

    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(webhook_url, json=payload)
                res.raise_for_status()
                return {
                    "status": "dispatched",
                    "response_status": res.status_code,
                    "payload": payload
                }
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="The workflow did not accept the lead.")

    # Simulation fallback for hackathon demonstration
    return {
        "status": "simulated_dispatch",
        "note": "N8N_WEBHOOK_URL not configured; returning verified dispatch contract payload for UI/n8n integration testing.",
        "payload": payload
    }


# =============================================================================
# 7. ML Scoring & Fact-Grounded Memo Endpoints
# =============================================================================
class MLScoreRequest(BaseModel):
    leads: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Optional rows in the live_candidates_for_ml.csv schema. If omitted, data/live_candidates_for_ml.csv is scored."
    )
    memo_top_k: int = Field(default=10, ge=0, le=50, description="Generate LLM memos for the top-K leads by blended score (0 = none)")
    refresh_memos: bool = Field(default=False, description="Regenerate memos even if cached")


async def _memo_for_lead(lead: Dict[str, Any], rank: Optional[int], total: Optional[int], refresh: bool = False,
                         allow_llm: bool = True) -> Dict[str, Any]:
    entity_id = lead.get("entity_id")
    ml_row = ml_service.explain_lead(entity_id)
    payload = build_memo_input(lead, rank, total, ml_row, ml_service.base_rates())
    memo = await generate_memo(entity_id, payload, refresh=refresh, allow_llm=allow_llm,
                               cost_tracker=CostTracker(max_budget_usd=settings.FOUNDERHUNT_ENRICH_BUDGET_USD))
    memo["ml_drivers"] = (ml_row or {}).get("contributions") or []
    return memo


def _is_operator(authorization: str | None) -> bool:
    token = settings.FOUNDERHUNT_WRITE_TOKEN
    return bool(token) and hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode())


@app.post(
    "/api/ml/score",
    tags=["ML"],
    summary="Run ML Ranking Model & Generate Top-K Memos",
    description=(
        "Scores leads with the ML ranking model, writes data/ml_predictions.csv (picked up by every other endpoint for "
        "ml_score and blended_score), then writes fact-grounded LLM memos for the top-K leads by blended score. "
        "Scoring is free and open; generating memos (memo_top_k > 0) calls OpenAI and needs the operator token."
    )
)
async def run_ml_scoring(req: MLScoreRequest = MLScoreRequest(), authorization: str | None = Header(None)):
    if req.memo_top_k > 0:
        require_operator(authorization)
    try:
        res = ml_service.run_scoring(leads=req.leads)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    feed = load_frontend_feed()  # now includes ml_score + blended_score, sorted by blended_score
    leads = feed.get("ranked_leads", [])
    top = leads[: req.memo_top_k]
    memos = await asyncio.gather(*[
        _memo_for_lead(lead, rank=i + 1, total=len(leads), refresh=req.refresh_memos) for i, lead in enumerate(top)
    ]) if top else []

    return {
        "scored": int(len(res)),
        "predictions_path": str(ml_service.ML_PRED_PATH),
        "scores": [
            {"company_id": r["company_id"], "ml_score": r["ml_score"], "ml_percentile": r["ml_percentile"], "ml_probability": r["ml_probability"],
             "model_used": r["model_used"], "ml_drivers": (r["contributions"] or [])[:5]}
            for r in res.to_dict(orient="records")
        ],
        "memos": [
            {"rank": i + 1, "entity_id": lead.get("entity_id"), "name": lead.get("name"),
             "blended_score": lead.get("blended_score"), "ml_score": lead.get("ml_score"), "memo": memo}
            for i, (lead, memo) in enumerate(zip(top, memos))
        ]
    }


@app.get(
    "/api/leads/{entity_id}/ml-memo",
    tags=["ML"],
    summary="Fact-Grounded LLM Memo for One Lead",
    description=(
        "One-liner, three strongest signals with source URLs, team, why it is early, devil's advocate risks and next step. "
        "Written only from facts already in the feed; cached on disk. Without the operator token only a cached memo or a "
        "free template memo is returned; with it, a missing memo is generated with OpenAI."
    )
)
async def get_lead_ml_memo(entity_id: str, refresh: bool = Query(False, description="Regenerate even if cached (operator only)"),
                           authorization: str | None = Header(None)):
    leads = load_frontend_feed().get("ranked_leads", [])
    idx = next((i for i, l in enumerate(leads) if l.get("entity_id") == entity_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found in ranked leads")
    operator = _is_operator(authorization)
    return await _memo_for_lead(leads[idx], rank=idx + 1, total=len(leads), refresh=refresh and operator, allow_llm=operator)


# Serve the prototype from the same origin as the API when the frontend is present.
frontend_directory = Path(__file__).resolve().parents[2] / "first-signal"
if (frontend_directory / "index.html").is_file():
    app.mount("/", StaticFiles(directory=frontend_directory, html=True), name="frontend")
