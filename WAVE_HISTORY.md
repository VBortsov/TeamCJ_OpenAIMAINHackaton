# WAVE Execution History & Pre-Submission Audit

## Wave Final — Pre-Submission Cleanup & Release Merge (2026-11-21 09:00 CET)

### 1. Architect Review of Overnight Wave (`20e6a0e` / `1e610ee`)
- **Status of Baseline Branches**:
  - `main` (`fcb781a`): Verified pristine production checkpoint with internal spend metrics removed from partner dashboard, clean SVG diagram added, and zero JWT leaks.
  - `feat/video-tech-scene` (`1e610ee`): Completed animated 41s tech scene (`docs/video_tech_scene.html`), passed Google Chrome headless Playwright QA across 6 timestamps (`t=4.0, 12.0, 20.0, 29.0, 36.0, 40.5s`) with 0 text overflow, 0 element collision, and margins $\ge 48\text{px}$.
- **Pre-Merge Audit of `feat/scale-sources-250` (`20e6a0e`) uncovered 5 Data-Quality Blockers**:
  1. **Arbitrary `top_k=250` Truncation**: Truncated 113 surviving leads in SQLite, dropping flagship cached leads (`aiXopt`, `Athenyx Robotics`, `LLMify`, `Determion`) and reducing verified LinkedIn/GitHub links from 31/11 to 14/7.
  2. **Domain Collision on Incubator/Portal Hosts**: Shared portal domains (`tum-venture-labs.de`, `legaltechcolab.com`, `baystartup.de`) caused `generate_entity_id()` to collapse distinct teams into shared IDs (e.g. `Holloid`, `Autonomous Navy`).
  3. **Luma Host Leakage**: 17 event hosts/calendars with only `luma_events` signals (`Female Founders Switzerland Calendar`, `AI Pilled`, `AI BEAVERS`, `House of AI Hamburg`) leaked into `ranked_leads`.
  4. **Dirty Cities, Duplicate Entity & Scaleup Rounds**: `SOLISTIQ` retained city `"Würzburg Vertreten"`, duplicate entity `Dr. Amelie Reigl, TigerShark Science` had city `"Stuttgart Postfach"`, and large funded scaleups `Spott` (€21.5M) and `Expanse` (€4.5M seed) survived post-enrichment.
  5. **Utility GitHub Repos**: 4 curated list / MCP utility repos (`best-of-robot-simulators`, `slack-mcp-server`, `cve-mcp-server`, `esp-labs-web`) leaked into startup leads.

### 2. Agreed Architectural Decisions
1. **Prioritize Cached Leads**: In `backend/run_wave2.py`, sort candidates with existing `llm_cache/<entity_id>.json` ahead of uncached entities so no cached flagships are ever dropped, and raise offline rebuild ceiling to `top_k=500`.
2. **Tighten Gate 1**: Filter out `luma_events`-only entities (Luma feeds `events_feed.json` exclusively unless corroborated by startup/university/code sources) and reject utility/list repo patterns (`^best-of-`, `^awesome-`, `mcp-server`, `-web$`).
3. **Clean Normalization, Dedup & Post-Funding Gate**: Strip German legal/address noise tokens (`Vertreten`, `Postfach`, `c/o`, `GmbH`, `UG`) in `normalize_city`; reject entities with $\ge 3.0\text{M}$ private equity/VC funding in `funding_summary` (exempting public non-dilutive grants like EXIST, ERC, EIC, Horizon); deduplicate `TigerShark Science`.
4. **Protect Portal Domains**: Add `baystartup.de`, `tum-venture-labs.de`, `legaltechcolab.com`, `unternehmertum.de` to `PLATFORM_DOMAINS` and ensure adapters do not assign portal URLs as startup domain.
5. **Rebuild Offline ($0.00 Spend)**: Run `run_wave2.py --skip-ingestion --cache-only --top-k 500`, re-run `score_live.py`, generate Top-25 memos + verify `aiXopt` memo, update `README.md`, run `pytest -v`, merge both feature branches into `main`, push `main` to `origin`, and package final handoff folder.

### 3. Unverified Assumptions
- **None**: All architectural decisions confirmed directly with Web Architect (Option A) and user authorization.
