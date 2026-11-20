from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.api import main
from backend.enrichment.openai_enricher import EnrichedProfile


def test_live_evaluation_requires_operator_and_persists_accepted_results(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "FOUNDERHUNT_WRITE_TOKEN", "test-operator")
    monkeypatch.setattr(main, "FEED_PATH", tmp_path / "feed.json")
    monkeypatch.setattr(main, "LIVE_LEADS_DIR", tmp_path / "live")
    required_profile = EnrichedProfile(
        canonical_name="Verified Venture",
        description="Manufactures scientific inspection hardware.",
        citations=["https://example.com/venture"],
        inflection_why_now="A new research grant supports customer validation.",
    )
    enrich = AsyncMock(return_value=required_profile)
    monkeypatch.setattr(main.OpenAIEnricher, "enrich_async", enrich)
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer test-operator"}
    payload = {"domain_or_name": "https://verified-example.com"}

    assert client.post("/api/enrich/live", json=payload).status_code == 401
    assert client.post("/api/enrich/live", json={"domain_or_name": "http://localhost"}, headers=headers).status_code == 400
    enrich.assert_not_awaited()

    result = client.post("/api/enrich/live", json=payload, headers=headers)
    assert result.status_code == 200, result.text
    lead = result.json()
    assert lead["gate_passed"] is True
    assert client.get("/api/leads").json()["leads"][0]["entity_id"] == lead["entity_id"]
    assert client.get("/api/leads/" + lead["entity_id"]).json()["executive_summary"] == required_profile.description
    assert client.post("/api/enrich/live", json=payload, headers=headers).json()["discovered_at"] == lead["discovered_at"]


def test_shared_workspace_is_private_and_validates_saved_states(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "FOUNDERHUNT_WRITE_TOKEN", "test-operator")
    monkeypatch.setattr(main, "SAVED_WORKSPACE_PATH", tmp_path / "workspace.json")
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer test-operator"}
    state = {"decisions": {"qooda": "shortlist"}, "tracked": {"bjoern-poetter": "track"}}
    url = "/api/workspace/saved"

    assert client.get(url).status_code == 401
    assert client.post(url, json=state).status_code == 401
    assert client.get(url, headers=headers).json() == {"decisions": {}, "tracked": {}}
    assert client.post(url, json={"decisions": {"qooda": "invalid"}}, headers=headers).status_code == 400
    assert client.post(url, json=state, headers=headers).status_code == 200
    assert client.get(url, headers=headers).json() == state
