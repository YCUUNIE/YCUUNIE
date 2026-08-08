"""Self-healing: detection, diagnosis, recovery, rollback, simulations."""
import pytest

from backend.app.agents import get_runtime
from backend.app.recovery.self_healing import SelfHealingManager


@pytest.mark.asyncio
async def test_agent_recovery_resets_state():
    rt = get_runtime()
    rt.load_defaults()
    alex = rt.agents["alex"]
    alex.recovery_state = "error"
    alex.consecutive_errors = 3
    alex.set_target(100, 100)
    res = await rt.recover_agent(alex, "test")
    assert res["recovered"] is True
    assert alex.recovery_state == "healthy"
    assert alex.target_x is None
    assert alex.consecutive_errors == 0


@pytest.mark.asyncio
async def test_heal_pipeline_produces_record():
    get_runtime().load_defaults()
    healer = SelfHealingManager()
    record = await healer.heal(kind="agent_error", target="alex", problem="test problem")
    assert record["status"] in ("SUCCESS", "ROLLED_BACK")
    assert "diagnosis" in record
    assert record["plan"]


@pytest.mark.asyncio
async def test_checkpoint_and_rollback():
    rt = get_runtime()
    rt.load_defaults()
    healer = SelfHealingManager()
    alex = rt.agents["alex"]
    alex.x, alex.y = 111, 222
    cid = healer.repair_agent.create_checkpoint("t")
    alex.x, alex.y = 999, 999
    ok = await healer.repair_agent.rollback_checkpoint(cid)
    assert ok
    assert alex.x == 111 and alex.y == 222


@pytest.mark.asyncio
async def test_simulate_invalid_tool_is_rejected():
    get_runtime().load_defaults()
    healer = SelfHealingManager()
    res = await healer.simulate("invalid_tool")
    assert res["rejected"] is True


@pytest.mark.asyncio
async def test_repair_agent_cannot_read_forbidden_paths():
    healer = SelfHealingManager()
    # .env is explicitly forbidden even inside the workspace
    assert healer.repair_agent._code_path_allowed("../.env") is False
    assert healer.repair_agent._code_path_allowed("backend/app/main.py") is True
    assert healer.repair_agent.apply_patch("backend/app/x.py", "x", approved=False)["ok"] is False
