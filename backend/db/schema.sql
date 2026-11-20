-- ==============================================================================
-- MAIN Summit 2026 - HTGF Track: Early-Stage European VC Startup Sourcing Engine
-- Database Schema (Ready for Waves 1, 2, and 3 without breaking migrations)
-- ==============================================================================

PRAGMA foreign_keys = ON;

-- 1. Core Startup / Spin-off Entities
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,                       -- Deterministic UUIDv5 from normalized_domain or source:slug
    canonical_name TEXT NOT NULL,                     -- Cleaned entity name
    normalized_domain TEXT UNIQUE,                    -- eTLD+1 domain (e.g., 'syntheta.ai')
    website_url TEXT,                                 -- Canonical homepage URL
    legal_name TEXT,                                  -- Official Handelsregister / Commercial register name
    legal_form TEXT,                                  -- GmbH, AG, UG, SA, Ltd., etc.
    register_number TEXT,                             -- e.g., HRB 123456
    country_code TEXT,                                -- ISO 3166-1 alpha-2 (DE, CH, AT, NL, etc.)
    incorporation_date TEXT,                          -- YYYY-MM-DD
    domain_created_date TEXT,                         -- WHOIS domain creation date
    description TEXT,                                 -- Concise company/product summary
    raw_tags_json TEXT,                               -- JSON array of taxonomy tags/keywords
    funding_status TEXT DEFAULT 'unknown',            -- 'unknown', 'bootstrapped', 'pre_seed', 'seed', 'series_a_plus', 'blocked'
    lp_synergy_json TEXT,                             -- HTGF LP strategic matching analysis (Wave 2)
    audio_briefing_path TEXT,                         -- Path to 60-second ElevenLabs briefing audio (Wave 3)
    n8n_dispatched_at TEXT,                           -- ISO timestamp of dispatch to n8n webhook
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 2. Granular Signals Ingested Across Sources
CREATE TABLE IF NOT EXISTS source_signals (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,                        -- 'eth_rwth', 'openalex', 'hf_hub', 'luma_events'
    source_native_id TEXT NOT NULL,                   -- Upstream unique ID / slug / URL
    signal_date TEXT,                                 -- Timestamp or date of event/paper/model
    raw_metrics_json TEXT NOT NULL,                   -- JSON object with raw metrics (stars, citations, trendingScore, etc.)
    source_url TEXT NOT NULL,                         -- Source link for auditability
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_name, source_native_id)
);

-- 3. Academic & Technical Founders / Key Executives
CREATE TABLE IF NOT EXISTS founders (
    founder_id TEXT PRIMARY KEY,                      -- UUIDv5 from ORCID, OpenAlex author ID, or Name+Entity
    full_name TEXT NOT NULL,
    orcid_id TEXT,
    openalex_author_id TEXT,
    github_handle TEXT,
    hf_handle TEXT,
    linkedin_url TEXT,
    email TEXT,
    h_index INTEGER,
    cited_by_count INTEGER,
    ex_big_tech_tags_json TEXT,                       -- JSON array: ['Google DeepMind', 'Meta AI']
    elite_affiliations_json TEXT,                     -- JSON array: ['ETH Zurich', 'Max Planck']
    raw_bio TEXT
);

-- 4. Entity <-> Founder Linkage & Roles
CREATE TABLE IF NOT EXISTS entity_founder_links (
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    founder_id TEXT NOT NULL REFERENCES founders(founder_id) ON DELETE CASCADE,
    link_method TEXT NOT NULL,                        -- 'paper_coauthorship', 'hf_org_member', 'spin_off_roster', 'luma_host'
    link_confidence REAL NOT NULL,                    -- 0.0 to 1.0 confidence score
    is_geschaeftsfuehrer INTEGER DEFAULT 0,           -- 1 if confirmed managing director
    PRIMARY KEY (entity_id, founder_id)
);

-- 5. Blocklist for Late-Stage / Mega-Funded or Corporate Outliers
CREATE TABLE IF NOT EXISTS funding_blocklist (
    normalized_name TEXT PRIMARY KEY,
    normalized_domain TEXT,
    source TEXT,
    round_info TEXT,
    source_url TEXT
);

-- Indexes for Fast Querying & Scoring
CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities(normalized_domain);
CREATE INDEX IF NOT EXISTS idx_signals_entity ON source_signals(entity_id);
CREATE INDEX IF NOT EXISTS idx_signals_source ON source_signals(source_name);
CREATE INDEX IF NOT EXISTS idx_founders_name ON founders(full_name);
CREATE INDEX IF NOT EXISTS idx_links_founder ON entity_founder_links(founder_id);
