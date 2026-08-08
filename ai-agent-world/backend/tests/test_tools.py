"""Tools: allowlist validation, execution, invalid params."""
import pytest
from pydantic import ValidationError

from backend.app.agents.runtime import AgentRuntime
from backend.app.schemas import Decision, MoveToAction
from backend.app.tools import ToolContext, get_tool_registry


def _ctx(rt: AgentRuntime, agent):
    return ToolContext(agent=agent, world=rt.world, memory=rt.memory,
                       workflows=rt.workflows, agents_by_name=rt.by_name(),
                       deliver_message=rt._deliver_message)


@pytest.mark.asyncio
async def test_move_to_executes_and_sets_target():
    rt = AgentRuntime(); rt.load_defaults()
    alex = rt.agents["alex"]
    reg = get_tool_registry()
    res = await reg.execute(_ctx(rt, alex), MoveToAction(x=500, y=300))
    assert res["ok"]
    assert alex.target_x == 500 and alex.target_y == 300


@pytest.mark.asyncio
async def test_disallowed_tool_rejected():
    rt = AgentRuntime(); rt.load_defaults()
    alex = rt.agents["alex"]
    alex.allowed_tools = ["idle"]  # move_to not allowed
    reg = get_tool_registry()
    res = await reg.execute(_ctx(rt, alex), MoveToAction(x=1, y=1))
    assert not res["ok"]
    assert res["error"].startswith("tool_not_allowed")


def test_invalid_action_params_rejected_by_pydantic():
    with pytest.raises(ValidationError):
        MoveToAction(x="not-a-number", y=1)


def test_decision_defaults_to_idle():
    d = Decision()
    assert d.action.type == "idle"


@pytest.mark.asyncio
async def test_talk_to_unknown_agent_is_safe():
    rt = AgentRuntime(); rt.load_defaults()
    from backend.app.schemas import TalkToAgentAction
    alex = rt.agents["alex"]
    res = await get_tool_registry().execute(_ctx(rt, alex), TalkToAgentAction(target="Ghost", message="hi"))
    assert res["ok"] is False
    assert res["error"].startswith("unknown_agent")
