"""API + WebSocket + end-to-end integration test.

Exercises the full loop: an agent perceives, decides, acts, memory updates, and
an event is emitted that a WebSocket client receives.
"""
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoints(client):
    assert client.get("/api/health").json()["status"] == "healthy"
    detailed = client.get("/api/health/detailed").json()
    assert "subsystems" in detailed
    assert "agent_runtime" in detailed["subsystems"]


def test_state_and_agents(client):
    state = client.get("/api/state").json()
    assert {a["name"] for a in state["agents"]} == {"Alex", "Nova", "Echo"}
    assert state["world"]["locations"]


def test_chat_is_world_aware(client):
    r = client.post("/api/chat", json={"target": "Alex", "message": "Where is Nova?"}).json()
    assert r["type"] == "chat"
    assert "Nova" in r["responses"][0]["reply"]


def test_slash_command(client):
    r = client.post("/api/chat", json={"target": "System", "message": "/agents"}).json()
    assert r["type"] == "system"
    assert "Alex" in r["responses"][0]["reply"]


def test_providers_endpoint(client):
    r = client.get("/api/providers").json()
    assert "local" in r["status"]


def test_create_agent_appears_in_world(client):
    payload = {"data": {"id": "mira", "name": "Mira", "role": "Merchant",
                        "personality": ["friendly", "calculating"], "goals": ["trade resources"],
                        "provider": {"provider": "local"}}}
    created = client.post("/api/agents", json=payload).json()
    assert created["name"] == "Mira"
    names = {a["name"] for a in client.get("/api/agents").json()}
    assert "Mira" in names


def test_websocket_receives_init_and_events(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "init"
        assert "state" in msg["data"]
        # trigger an event via chat, then expect an event frame
        client.post("/api/chat", json={"target": "Alex", "message": "hello"})
        got_event = False
        for _ in range(20):
            frame = ws.receive_json()
            if frame.get("type") == "event":
                got_event = True
                break
        assert got_event


def test_repair_simulation_endpoint(client):
    r = client.post("/api/repair/simulate/agent_error").json()
    assert r["triggered"] == "agent_error"
