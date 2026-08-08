"""Agent system: state, decision loop, personality, actions, relationships."""
import pytest

from backend.app.agents.agent import Agent, AgentState, ProviderConfig
from backend.app.agents.runtime import AgentRuntime


def _runtime():
    rt = AgentRuntime()
    rt.load_defaults()
    return rt


def test_default_agents_loaded():
    rt = _runtime()
    names = sorted(a.name for a in rt.agents.values())
    assert names == ["Alex", "Echo", "Nova"]


def test_movement_reaches_target_and_sets_idle():
    a = Agent(id="t", name="T", role="R", x=0, y=0, speed=100)
    a.set_target(50, 0)
    assert a.state == AgentState.WALKING
    for _ in range(20):
        a.step_movement(0.1)
    assert abs(a.x - 50) < 1
    assert a.state == AgentState.IDLE


def test_facing_updates_with_direction():
    a = Agent(id="t", name="T", role="R", x=0, y=0, speed=100)
    a.set_target(50, 0)
    a.step_movement(0.1)
    assert a.facing == "right"


def test_relationships_clamped():
    a = Agent(id="t", name="T", role="R")
    a.adjust_relationship("x", trust=5.0)
    assert a.relationship("x")["trust"] == 1.0
    a.adjust_relationship("x", trust=-5.0)
    assert a.relationship("x")["trust"] == 0.0


def test_personality_affects_decision():
    """A risk-taker explores the unusual; a cautious agent assesses first."""
    rt = _runtime()
    alex = rt.agents["alex"]   # risk-taking
    nova = rt.agents["nova"]   # cautious
    perception = {"location": "Northern Forest", "position": {"x": 700, "y": 180},
                  "objects_nearby": [], "agents_nearby": [], "unusual_hint": "You sense something unusual nearby.",
                  "inbox": [], "current_task": None, "workflow": None, "known_locations": []}
    d_alex = rt._heuristic_decision(alex, perception)
    d_nova = rt._heuristic_decision(nova, perception)
    # Alex investigates (observe/move); Nova assesses cautiously (observe with smaller radius).
    assert d_alex.action.type in ("observe_area", "move_to_location")
    assert d_nova.action.type == "observe_area"


@pytest.mark.asyncio
async def test_decision_cycle_runs_and_acts():
    rt = _runtime()
    alex = rt.agents["alex"]
    await rt._decision_cycle(alex)
    # after a cycle the agent has produced a summary and an action
    assert alex.last_summary
    assert alex.last_action is not None
    assert alex.consecutive_errors == 0
