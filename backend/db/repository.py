"""
Database Repository: SQLite persistence, schema initialization, domain normalization,
and upsert helpers for entities, signals, and founders.
"""

from __future__ import annotations
import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import tldextract

from backend.config import settings

logger = logging.getLogger(__name__)

# Configure tldextract with local cache
_extractor = tldextract.TLDExtract(
    cache_dir=str(settings.DATA_DIR / "tld_cache"),
    include_psl_private_domains=True
)

UUID_NAMESPACE = uuid.UUID("3c87e5b2-3e28-4e89-9a73-983b6329e46a")


def normalize_domain(url_or_domain: Optional[str]) -> Optional[str]:
    """
    Extract and normalize the top domain under public suffix (eTLD+1).
    Example: 'https://sub.portal.deeptech.de/index.html' -> 'deeptech.de'
    """
    if not url_or_domain:
        return None
    
    cleaned = url_or_domain.strip().lower()
    if not cleaned or cleaned in {"none", "null", "undefined", "n/a", "localhost"}:
        return None

    # Strip protocol prefix for cleaner regex parsing if needed
    cleaned = re.sub(r"^[a-zA-Z]+://", "", cleaned)
    cleaned = cleaned.split("/")[0].split("?")[0].split("#")[0].strip()

    if not cleaned:
        return None

    try:
        extracted = _extractor(cleaned)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}".lower()
        elif extracted.domain:
            return extracted.domain.lower()
    except Exception as e:
        logger.debug("Failed to extract domain from %s: %s", url_or_domain, e)

    return None


def slugify(text: str) -> str:
    """Standardize string into a lowercase slug."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


def generate_entity_id(
    canonical_name: str,
    normalized_domain: Optional[str] = None,
    source_name: Optional[str] = None,
    source_native_id: Optional[str] = None
) -> str:
    """
    Deterministic UUIDv5.
    Priority:
    1. normalized_domain if present
    2. source_name:source_native_id if present
    3. canonical_name slug
    """
    if normalized_domain:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, normalized_domain))
    
    if source_name and source_native_id:
        seed = f"urn:source:{source_name}:{source_native_id}"
        return str(uuid.uuid5(UUID_NAMESPACE, seed))

    seed = f"urn:entity:{slugify(canonical_name)}"
    return str(uuid.uuid5(UUID_NAMESPACE, seed))


def generate_founder_id(
    full_name: str,
    orcid_id: Optional[str] = None,
    openalex_author_id: Optional[str] = None,
    github_handle: Optional[str] = None,
    hf_handle: Optional[str] = None
) -> str:
    """Deterministic UUIDv5 for founders based on strongest unique identifier."""
    if orcid_id:
        return str(uuid.uuid5(UUID_NAMESPACE, f"orcid:{orcid_id.strip()}"))
    if openalex_author_id:
        return str(uuid.uuid5(UUID_NAMESPACE, f"openalex:{openalex_author_id.strip()}"))
    if github_handle:
        return str(uuid.uuid5(UUID_NAMESPACE, f"gh:{github_handle.strip().lower()}"))
    if hf_handle:
        return str(uuid.uuid5(UUID_NAMESPACE, f"hf:{hf_handle.strip().lower()}"))
    
    return str(uuid.uuid5(UUID_NAMESPACE, f"name:{slugify(full_name)}"))


import contextlib

@contextlib.contextmanager
def get_db_connection(db_path: Path | str):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
    finally:
        conn.close()

class DatabaseRepository:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def get_connection(self):
        return get_db_connection(self.db_path)

    def init_schema(self) -> None:
        """Initialize tables and indexes from schema.sql."""
        schema_file = Path(__file__).parent / "schema.sql"
        if not schema_file.exists():
            raise FileNotFoundError(f"Schema file not found at {schema_file}")
        
        with schema_file.open("r", encoding="utf-8") as f:
            schema_sql = f.read()

        with get_db_connection(self.db_path) as conn:
            conn.executescript(schema_sql)
            conn.commit()
        logger.info("Database schema initialized at %s", self.db_path)

    def upsert_entity(
        self,
        canonical_name: str,
        normalized_domain: Optional[str] = None,
        website_url: Optional[str] = None,
        legal_name: Optional[str] = None,
        legal_form: Optional[str] = None,
        register_number: Optional[str] = None,
        country_code: Optional[str] = None,
        incorporation_date: Optional[str] = None,
        domain_created_date: Optional[str] = None,
        description: Optional[str] = None,
        raw_tags: Optional[List[str]] = None,
        funding_status: str = "unknown",
        lp_synergy: Optional[Dict[str, Any]] = None,
        source_name: Optional[str] = None,
        source_native_id: Optional[str] = None
    ) -> str:
        """
        Upsert entity record. If domain matches existing entity, merge fields.
        Returns the entity_id.
        """
        norm_domain = normalize_domain(normalized_domain or website_url)
        raw_tags_json = json.dumps(raw_tags or [], ensure_ascii=False)
        lp_synergy_json = json.dumps(lp_synergy or {}, ensure_ascii=False) if lp_synergy else None

        with self.get_connection() as conn:
            cursor = conn.cursor()

            existing_id = None
            if norm_domain:
                cursor.execute("SELECT entity_id FROM entities WHERE normalized_domain = ?", (norm_domain,))
                row = cursor.fetchone()
                if row:
                    existing_id = row["entity_id"]

            entity_id = existing_id or generate_entity_id(
                canonical_name=canonical_name,
                normalized_domain=norm_domain,
                source_name=source_name,
                source_native_id=source_native_id
            )

            # Insert or Update
            sql = """
            INSERT INTO entities (
                entity_id, canonical_name, normalized_domain, website_url,
                legal_name, legal_form, register_number, country_code,
                incorporation_date, domain_created_date, description,
                raw_tags_json, funding_status, lp_synergy_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                canonical_name = COALESCE(excluded.canonical_name, entities.canonical_name),
                normalized_domain = COALESCE(excluded.normalized_domain, entities.normalized_domain),
                website_url = COALESCE(excluded.website_url, entities.website_url),
                country_code = COALESCE(excluded.country_code, entities.country_code),
                description = CASE
                    WHEN entities.description IS NULL OR length(entities.description) < length(excluded.description)
                    THEN excluded.description ELSE entities.description END,
                raw_tags_json = excluded.raw_tags_json,
                updated_at = CURRENT_TIMESTAMP
            """
            cursor.execute(sql, (
                entity_id, canonical_name, norm_domain, website_url,
                legal_name, legal_form, register_number, country_code,
                incorporation_date, domain_created_date, description,
                raw_tags_json, funding_status, lp_synergy_json
            ))
            conn.commit()

        return entity_id

    def upsert_signal(
        self,
        entity_id: str,
        source_name: str,
        source_native_id: str,
        signal_date: Optional[str],
        raw_metrics: Dict[str, Any],
        source_url: str
    ) -> int:
        """Upsert a source signal tied to an entity."""
        raw_metrics_json = json.dumps(raw_metrics or {}, ensure_ascii=False)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = """
            INSERT INTO source_signals (
                entity_id, source_name, source_native_id, signal_date,
                raw_metrics_json, source_url
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_name, source_native_id) DO UPDATE SET
                entity_id = excluded.entity_id,
                signal_date = COALESCE(excluded.signal_date, source_signals.signal_date),
                raw_metrics_json = excluded.raw_metrics_json,
                source_url = excluded.source_url,
                ingested_at = CURRENT_TIMESTAMP
            RETURNING signal_id
            """
            cursor.execute(sql, (
                entity_id, source_name, source_native_id, signal_date,
                raw_metrics_json, source_url
            ))
            row = cursor.fetchone()
            conn.commit()
            return row["signal_id"] if row else 0

    def upsert_founder(
        self,
        full_name: str,
        orcid_id: Optional[str] = None,
        openalex_author_id: Optional[str] = None,
        github_handle: Optional[str] = None,
        hf_handle: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        h_index: Optional[int] = None,
        cited_by_count: Optional[int] = None,
        ex_big_tech_tags: Optional[List[str]] = None,
        elite_affiliations: Optional[List[str]] = None,
        raw_bio: Optional[str] = None
    ) -> str:
        """Upsert founder profile and return founder_id."""
        founder_id = generate_founder_id(
            full_name=full_name,
            orcid_id=orcid_id,
            openalex_author_id=openalex_author_id,
            github_handle=github_handle,
            hf_handle=hf_handle
        )
        ex_big_tech_json = json.dumps(ex_big_tech_tags or [], ensure_ascii=False)
        elite_affiliations_json = json.dumps(elite_affiliations or [], ensure_ascii=False)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = """
            INSERT INTO founders (
                founder_id, full_name, orcid_id, openalex_author_id,
                github_handle, hf_handle, linkedin_url, email,
                h_index, cited_by_count, ex_big_tech_tags_json,
                elite_affiliations_json, raw_bio
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(founder_id) DO UPDATE SET
                full_name = COALESCE(excluded.full_name, founders.full_name),
                orcid_id = COALESCE(excluded.orcid_id, founders.orcid_id),
                openalex_author_id = COALESCE(excluded.openalex_author_id, founders.openalex_author_id),
                github_handle = COALESCE(excluded.github_handle, founders.github_handle),
                hf_handle = COALESCE(excluded.hf_handle, founders.hf_handle),
                linkedin_url = COALESCE(excluded.linkedin_url, founders.linkedin_url),
                h_index = COALESCE(excluded.h_index, founders.h_index),
                cited_by_count = COALESCE(excluded.cited_by_count, founders.cited_by_count),
                ex_big_tech_tags_json = excluded.ex_big_tech_tags_json,
                elite_affiliations_json = excluded.elite_affiliations_json,
                raw_bio = COALESCE(excluded.raw_bio, founders.raw_bio)
            """
            cursor.execute(sql, (
                founder_id, full_name, orcid_id, openalex_author_id,
                github_handle, hf_handle, linkedin_url, email,
                h_index, cited_by_count, ex_big_tech_json,
                elite_affiliations_json, raw_bio
            ))
            conn.commit()

        return founder_id

    def link_entity_founder(
        self,
        entity_id: str,
        founder_id: str,
        link_method: str,
        link_confidence: float = 0.8,
        is_geschaeftsfuehrer: int = 0
    ) -> None:
        """Create or update link between entity and founder."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = """
            INSERT INTO entity_founder_links (
                entity_id, founder_id, link_method, link_confidence, is_geschaeftsfuehrer
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, founder_id) DO UPDATE SET
                link_method = excluded.link_method,
                link_confidence = MAX(entity_founder_links.link_confidence, excluded.link_confidence),
                is_geschaeftsfuehrer = MAX(entity_founder_links.is_geschaeftsfuehrer, excluded.is_geschaeftsfuehrer)
            """
            cursor.execute(sql, (
                entity_id, founder_id, link_method, link_confidence, is_geschaeftsfuehrer
            ))
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Summary metrics for logging and UI reporting."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            total_entities = cursor.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            total_signals = cursor.execute("SELECT COUNT(*) FROM source_signals").fetchone()[0]
            total_founders = cursor.execute("SELECT COUNT(*) FROM founders").fetchone()[0]
            total_links = cursor.execute("SELECT COUNT(*) FROM entity_founder_links").fetchone()[0]

            signals_by_source = {}
            for row in cursor.execute(
                "SELECT source_name, COUNT(*) as cnt FROM source_signals GROUP BY source_name"
            ).fetchall():
                signals_by_source[row["source_name"]] = row["cnt"]

            country_distribution = {}
            for row in cursor.execute(
                "SELECT COALESCE(country_code, 'Unknown') as country, COUNT(*) as cnt FROM entities GROUP BY country"
            ).fetchall():
                country_distribution[row["country"]] = row["cnt"]

        return {
            "total_entities": total_entities,
            "total_signals": total_signals,
            "total_founders": total_founders,
            "total_links": total_links,
            "signals_by_source": signals_by_source,
            "country_distribution": country_distribution
        }

    def export_all_candidates(self) -> List[Dict[str, Any]]:
        """
        Export denormalized candidate objects with attached signals and founders
        for downstream integration (Wave 2 scoring, n8n, UI).
        """
        candidates: List[Dict[str, Any]] = []
        with self.get_connection() as conn:
            cursor = conn.cursor()
            entities_rows = cursor.execute("SELECT * FROM entities ORDER BY created_at DESC").fetchall()

            for erow in entities_rows:
                ent = dict(erow)
                ent_id = ent["entity_id"]

                # Parse JSON fields
                try:
                    ent["raw_tags"] = json.loads(ent["raw_tags_json"]) if ent.get("raw_tags_json") else []
                except Exception:
                    ent["raw_tags"] = []

                try:
                    ent["lp_synergy"] = json.loads(ent["lp_synergy_json"]) if ent.get("lp_synergy_json") else {}
                except Exception:
                    ent["lp_synergy"] = {}

                # Attach signals
                signals_rows = cursor.execute(
                    "SELECT * FROM source_signals WHERE entity_id = ? ORDER BY signal_date DESC, ingested_at DESC",
                    (ent_id,)
                ).fetchall()
                signals = []
                for s in signals_rows:
                    s_dict = dict(s)
                    try:
                        s_dict["raw_metrics"] = json.loads(s_dict["raw_metrics_json"])
                    except Exception:
                        s_dict["raw_metrics"] = {}
                    signals.append(s_dict)
                ent["signals"] = signals

                # Attach founders
                founders_rows = cursor.execute("""
                    SELECT f.*, l.link_method, l.link_confidence, l.is_geschaeftsfuehrer
                    FROM founders f
                    JOIN entity_founder_links l ON f.founder_id = l.founder_id
                    WHERE l.entity_id = ?
                """, (ent_id,)).fetchall()
                founders = []
                for f in founders_rows:
                    f_dict = dict(f)
                    try:
                        f_dict["ex_big_tech_tags"] = json.loads(f_dict["ex_big_tech_tags_json"]) if f_dict.get("ex_big_tech_tags_json") else []
                    except Exception:
                        f_dict["ex_big_tech_tags"] = []
                    try:
                        f_dict["elite_affiliations"] = json.loads(f_dict["elite_affiliations_json"]) if f_dict.get("elite_affiliations_json") else []
                    except Exception:
                        f_dict["elite_affiliations"] = []
                    founders.append(f_dict)
                ent["founders"] = founders

                candidates.append(ent)

        return candidates
