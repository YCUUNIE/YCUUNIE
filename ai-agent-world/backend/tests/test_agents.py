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


@pytest.mark.asyncio
async def test_discovery_announced_once_no_loop():
    """Regression: Alex must not re-announce the same discovery every cycle."""
    from backend.app.agents.agent import ProviderConfig
    from backend.app.events import event_bus

    rt = _runtime()
    for a in rt.agents.values():
        a.provider = ProviderConfig(provider="local", fallbacks=["local"])
    rt.world.discover("structure_1", "Alex")

    counts = {"announce": 0, "invite": 0}

    async def cap(e):
        if e.type == "agent.talked":
            m = e.data.get("message", "")
            if "strange structure near the northern forest!" in m:
                counts["announce"] += 1
            if "Can you investigate" in m:
                counts["invite"] += 1

    event_bus.subscribe(cap)
    for _ in range(25):
        for a in list(rt.agents.values()):
            await rt._decision_cycle(a)

    assert counts["announce"] == 1
    assert counts["invite"] == 1


@pytest.mark.asyncio
async def test_loop_guard_breaks_repeated_action():
    from backend.app.schemas import Decision
    rt = _runtime()
    echo = rt.agents["echo"]
    same = Decision(action={"type": "observe_area", "radius": 200})
    d1 = await rt._loop_guard(echo, same)
    d2 = await rt._loop_guard(echo, same)
    d3 = await rt._loop_guard(echo, same)  # third identical -> should be replaced
    assert echo.loop_repeat == 0  # reset after break
    # the guard returns a (possibly different) valid decision, never crashes
    assert d3.action is not None
