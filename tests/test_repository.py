"""
Unit tests for database repository, domain normalization, and schema operations.
"""

import tempfile
from pathlib import Path
import pytest

from backend.db.repository import (
    DatabaseRepository,
    normalize_domain,
    generate_entity_id,
    generate_founder_id,
    slugify
)


def test_normalize_domain():
    assert normalize_domain("https://sub.deeptech.de/path?arg=1") == "deeptech.de"
    assert normalize_domain("http://www.hydken.com/") == "hydken.com"
    assert normalize_domain("floatz.ai") == "floatz.ai"
    assert normalize_domain("https://frameway.ai/index.html") == "frameway.ai"
    assert normalize_domain("invalid://") is None
    assert normalize_domain("") is None
    assert normalize_domain(None) is None


def test_slugify():
    assert slugify("Hydken GmbH & Co. KG") == "hydken-gmbh-co-kg"
    assert slugify("ETH Zurich Spin-off!") == "eth-zurich-spin-off"


def test_deterministic_uuids():
    id1 = generate_entity_id("Hydken GmbH", normalized_domain="hydken.com")
    id2 = generate_entity_id("Hydken", normalized_domain="hydken.com")
    assert id1 == id2, "Domain-based UUIDs must be identical regardless of company name variant"

    f1 = generate_founder_id("Jane Doe", orcid_id="0000-0002-1825-0097")
    f2 = generate_founder_id("Jane Doe", orcid_id="0000-0002-1825-0097")
    assert f1 == f2, "ORCID-based founder UUIDs must be deterministic"


def test_repository_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_scout.db"
        repo = DatabaseRepository(db_path)

        # 1. Upsert Entity
        ent_id = repo.upsert_entity(
            canonical_name="aiXopt",
            normalized_domain="aixopt.ai",
            website_url="https://aixopt.ai",
            legal_form="GmbH",
            country_code="DE",
            description="Industrial small-data AI parameter optimization.",
            raw_tags=["DeepTech", "Industrial AI", "RWTH Aachen"]
        )
        assert ent_id is not None

        # 2. Upsert Signal
        sig_id = repo.upsert_signal(
            entity_id=ent_id,
            source_name="eth_rwth",
            source_native_id="rwth:2026:aixopt",
            signal_date="2026-01-01",
            raw_metrics={"award": "Spin-off Award", "year": 2026},
            source_url="https://rwth-aachen.de/award"
        )
        assert sig_id > 0

        # 3. Upsert Founder & Link
        founder_id = repo.upsert_founder(
            full_name="Dr. Max Mustermann",
            elite_affiliations=["RWTH Aachen University"],
            h_index=14
        )
        repo.link_entity_founder(
            entity_id=ent_id,
            founder_id=founder_id,
            link_method="spin_off_roster",
            link_confidence=0.9
        )

        # 4. Check Stats
        stats = repo.get_stats()
        assert stats["total_entities"] == 1
        assert stats["total_signals"] == 1
        assert stats["total_founders"] == 1
        assert stats["total_links"] == 1
        assert stats["country_distribution"].get("DE") == 1

        # 5. Export Candidates
        export = repo.export_all_candidates()
        assert len(export) == 1
        cand = export[0]
        assert cand["canonical_name"] == "aiXopt"
        assert len(cand["signals"]) == 1
        assert len(cand["founders"]) == 1
        assert cand["founders"][0]["full_name"] == "Dr. Max Mustermann"
