"""
ML memo agent: one OpenAI call per lead that writes a short investment memo strictly from facts
already collected by the pipeline (feed lead, scores, model drivers, base rates).
The agent never searches and never invents: it explains what is in the JSON it receives.

Key: OPENAI_API_KEY from .env / environment (backend.config.settings). Model: OPENAI_MEMO_MODEL in .env or the environment.
Memos are cached on disk in data/memo_cache/<entity_id>.json, keyed by a hash of the input facts.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from backend.config import settings
from backend.enrichment.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

MEMO_MODEL = os.getenv("OPENAI_MEMO_MODEL") or settings.OPENAI_MEMO_MODEL  # env var or .env
MEMO_CACHE_DIR = settings.DATA_DIR / "memo_cache"

SYSTEM_PROMPT = """You are an analyst at High-Tech Gruenderfonds (HTGF), a German seed investor that wants to meet
startups BEFORE their first funding round. Write a short investment memo for one startup.

You receive one JSON object with everything we know. Rules, no exceptions:
1. Every claim must rest on a fact in the JSON. If there is no fact for something, do not write about it.
2. Cite sources only with URLs that appear in the JSON. Never invent URLs, names, numbers or dates.
   A signal gets a `source_url` only if that URL is attached to that same fact in the JSON (e.g. the `source_url`
   of the entry in `signals`, or a founder's own link). Scores, ranks and model drivers have no URL: use null.
   Prefer signals that have a source over scores.
3. A missing, null or empty field means UNKNOWN, not "no". Say "unknown" or put it under risks, never "has no X".
4. `ml_score` is a 0-100 model score where 50 is the typical startup in our reference cohort. `ml_percentile` is
   the share of that cohort the lead scores above. Neither is a probability of success: never write "<N>% chance".
   You may write "model score <ml_score> of 100" or, if `ml_percentile` is below 99, "scores above <ml_percentile>%
   of the reference cohort"; at 99 or more write "at the top of the reference cohort". For the rank use only
   `scores.rank_today` and `scores.leads_today`; if they are absent, do not mention a rank.
5. `ml_drivers` are flags in our data that pushed the ML score up (+) or down (-); explain them in plain words.
   A driver is a model input, not proof: if `founders` does not show who it refers to (e.g. no founder with a CTO
   role), say the flag is set but not confirmed by the founder records, and do not state it as a fact. `not_verified_would_raise_ml_score` lists founder facts we could not verify: mention them only as unknowns.
   `base_rates` come from 688 accelerator-backed European startups (2014-2024): quote them as
   "in our training data" and treat them as correlations, not guarantees.
6. Be concrete and short. No hype words. English.
7. `next_step` names a team at HTGF (e.g. "HTGF Industrial Tech team") and one concrete question for the founders.
   Do not invent names of HTGF employees.
"""

MEMO_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["one_liner", "top_signals", "team", "why_early", "devils_advocate_risks", "next_step"],
    "properties": {
        "one_liner": {"type": "string", "description": "What the company does and why look at it now. Max 30 words."},
        "top_signals": {
            "type": "array",
            "description": "The three strongest signals, strongest first.",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["signal", "source_url"],
                "properties": {"signal": {"type": "string"},
                               "source_url": {"type": ["string", "null"], "description": "URL copied from the input, else null"}},
            },
        },
        "team": {"type": "string", "description": "Who the founders are and what is verified about them."},
        "why_early": {"type": "string", "description": "What shows no priced round has happened yet; 'unknown' if nothing does."},
        "devils_advocate_risks": {"type": "array", "items": {"type": "string"}, "description": "One to three risks or unknowns."},
        "next_step": {"type": "string"},
    },
}


def _prune(obj: Any) -> Any:
    """Drop null / empty values so the model cannot mistake 'unknown' for a fact."""
    if isinstance(obj, dict):
        out = {k: _prune(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [v for v in (_prune(x) for x in obj) if v not in (None, "", [], {})]
    return obj


def _driver(d: Dict[str, Any]) -> Dict[str, Any]:
    what = f"{int(d['value'])} founder(s) known" if d["feature"] == "n_founders" else d["label"]
    return {"what": what, "direction": "+" if d["contribution"] > 0 else "-", "weight": d["contribution"], "feature": d["feature"]}


def _split_drivers(drivers: list) -> tuple[list, list]:
    """Present facts (flag = 1, founder count) explain the score. Flags at 0 are not facts about the company:
    only founder flags that would raise the score are passed on, as 'not verified' (0 also covers unknown)."""
    present = [_driver(d) for d in drivers if (d["value"] or d["feature"] == "n_founders") and abs(d["contribution"]) >= 0.05]
    unverified = [d["label"] for d in drivers
                  if not d["value"] and d["contribution"] <= -0.05 and (d["feature"].startswith("fd_") or d["feature"] == "has_cto_founder")]
    return present[:8], unverified[:4]


def build_memo_input(lead: Dict[str, Any], rank: Optional[int], total: Optional[int],
                     ml_row: Optional[Dict[str, Any]], base_rates: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the fact sheet for one lead from the frontend feed LeadObject + ML output."""
    bd = lead.get("score_breakdown") or {}
    drivers = (ml_row or {}).get("contributions") or []
    present, unverified = _split_drivers(drivers)
    used = {d["feature"] for d in present}
    rates = {f: r for f, r in (base_rates.get("features") or {}).items() if f in used}
    payload = {
        "company": {k: lead.get(k) for k in (
            "name", "legal_name", "legal_form", "website_url", "country_code", "city", "register_number",
            "founding_year", "htgf_vertical", "description", "earliness_badge", "funding_summary")},
        "scores": {
            "rank_today": rank, "leads_today": total,
            "blended_score": lead.get("blended_score"),
            "deterministic_score": bd.get("final_score", lead.get("deterministic_score")),
            "ml_score": lead.get("ml_score"),
            "ml_percentile": (ml_row or {}).get("ml_percentile"),
            "deterministic_breakdown": {k: bd.get(k) for k in (
                "team_pedigree", "commercial_intent", "technical_moat", "multi_signal_convergence", "htgf_mandate_fit")},
        },
        "deterministic_drivers": bd.get("drivers"),
        "evidence_urls": bd.get("evidence_urls"),
        "ml_drivers": present,
        "not_verified_would_raise_ml_score": unverified,
        "base_rates": {"overall_rate": base_rates.get("overall_rate"), "label": base_rates.get("label"), "by_feature": rates},
        "signals": lead.get("discovered_from"),
        "founders": lead.get("founders"),
        "lp_synergy_matches": lead.get("lp_synergy_matches"),
        "inflection_why_now": lead.get("inflection_why_now"),
        "known_risks": lead.get("devils_advocate_risks"),
    }
    return _prune(payload)


def _cache_file(entity_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(entity_id))
    return MEMO_CACHE_DIR / f"{safe}.json"


def load_cached_memo(entity_id: str) -> Optional[Dict[str, Any]]:
    f = _cache_file(entity_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def memo_input_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_current_memo(entity_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The cached memo, but only while it still describes the lead's current facts.
    After a re-enrichment the facts change, and an old memo would name founders the data no longer has."""
    cached = load_cached_memo(entity_id)
    return cached if cached and cached.get("input_hash") == memo_input_hash(payload) else None


def fallback_memo(p: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic memo from the same facts. Used when OpenAI is unavailable; states only supplied facts."""
    c = p.get("company", {})
    sigs = [{"signal": s.get("summary") or s.get("source_label") or s.get("source_name", ""), "source_url": s.get("source_url")}
            for s in p.get("signals", [])][:3]
    for d in p.get("deterministic_drivers", []):
        if len(sigs) >= 3:
            break
        sigs.append({"signal": d, "source_url": None})
    founders = p.get("founders") or []
    team = "; ".join(f"{f.get('full_name')}" + (f" ({f.get('role')})" if f.get("role") else "") for f in founders if f.get("full_name"))
    downs = [f"ML model: '{d['what']}' lowers the score" for d in p.get("ml_drivers", []) if d["direction"] == "-"][:1]
    return {
        "one_liner": f"{c.get('name', 'Unknown')}: {(c.get('description') or 'description unknown')[:200]}",
        "top_signals": sigs,
        "team": team or "Founders unknown.",
        "why_early": c.get("funding_summary") or c.get("earliness_badge") or "unknown",
        "devils_advocate_risks": (p.get("known_risks") or [])[:2] + downs or ["Too few verified facts to assess."],
        "next_step": "HTGF sector team: ask the founders about current funding status and first customers.",
    }


async def generate_memo(entity_id: str, payload: Dict[str, Any], refresh: bool = False,
                        cost_tracker: Optional[CostTracker] = None, allow_llm: bool = True) -> Dict[str, Any]:
    """Return the memo for one lead: cached if the facts did not change, else one OpenAI call."""
    digest = memo_input_hash(payload)
    cached = load_cached_memo(entity_id)
    if cached and not refresh and cached.get("memo_source", "").startswith("openai") and cached.get("input_hash") == digest:
        return cached

    tracker = cost_tracker or CostTracker()
    memo: Dict[str, Any]
    if not allow_llm:
        memo = {**fallback_memo(payload), "memo_source": "fallback_template", "memo_note": "LLM memo not generated yet"}
    elif not settings.OPENAI_API_KEY:
        memo = {**fallback_memo(payload), "memo_source": "fallback_template", "memo_note": "OPENAI_API_KEY not set"}
    elif not tracker.can_execute_live_call(expected_cost=0.002):
        memo = {**fallback_memo(payload), "memo_source": "fallback_template", "memo_note": "Budget cap reached or cache-only mode"}
    else:
        try:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            resp = await client.chat.completions.create(
                model=MEMO_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "investment_memo", "strict": True, "schema": MEMO_SCHEMA}},
            )
            memo = json.loads(resp.choices[0].message.content)
            memo["top_signals"] = memo.get("top_signals", [])[:3]
            memo["devils_advocate_risks"] = memo.get("devils_advocate_risks", [])[:3]
            memo["memo_source"] = f"openai:{MEMO_MODEL}"
            usage = getattr(resp, "usage", None)
            if usage:
                tracker.record_live_call(entity_id, payload.get("company", {}).get("name", ""),
                                         usage.prompt_tokens, usage.completion_tokens, 0)
        except Exception as e:  # keep the demo alive if OpenAI is unreachable
            logger.warning("Memo generation failed for %s: %s", entity_id, e)
            memo = {**fallback_memo(payload), "memo_source": "fallback_template",
                    "memo_note": f"{type(e).__name__}: {str(e)[:160]}"}

    memo["entity_id"] = entity_id
    memo["input_hash"] = digest
    if memo["memo_source"].startswith("openai"):
        MEMO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(entity_id).write_text(json.dumps(memo, ensure_ascii=False, indent=2), encoding="utf-8")
    return memo
