"""Memory: Obsidian read/write, search, retrieval, importance, sandbox."""
import pytest

from backend.app.memory.base import Memory
from backend.app.memory.obsidian import ObsidianMemoryProvider, ObsidianVault


@pytest.mark.asyncio
async def test_remember_and_recall(tmp_vault_path):
    vault = ObsidianVault(vault_path=tmp_vault_path, root_folder="AI-Agents")
    provider = ObsidianMemoryProvider(vault=vault)
    await provider.remember(Memory.new("Alex", "The northern forest hides a strange structure.", "high"))
    await provider.remember(Memory.new("Alex", "The river flows to the west.", "low"))
    results = await provider.recall("Alex", "strange structure forest", limit=1)
    assert results
    assert "structure" in results[0].content


@pytest.mark.asyncio
async def test_memory_written_to_disk(tmp_vault_path):
    vault = ObsidianVault(vault_path=tmp_vault_path, root_folder="AI-Agents")
    provider = ObsidianMemoryProvider(vault=vault)
    await provider.remember(Memory.new("Nova", "Built a workshop wall.", "medium"))
    files = list((vault.root / "Memories" / "Nova").glob("*.md"))
    assert files
    assert "workshop" in files[0].read_text()


@pytest.mark.asyncio
async def test_importance_boosts_ranking(tmp_vault_path):
    vault = ObsidianVault(vault_path=tmp_vault_path, root_folder="AI-Agents")
    provider = ObsidianMemoryProvider(vault=vault)
    await provider.remember(Memory.new("Echo", "wood resource note", "low"))
    await provider.remember(Memory.new("Echo", "wood resource note", "critical"))
    results = await provider.recall("Echo", "wood resource", limit=1)
    assert results[0].importance == "critical"


def test_vault_sandbox_blocks_escape(tmp_vault_path):
    vault = ObsidianVault(vault_path=tmp_vault_path, root_folder="AI-Agents")
    with pytest.raises(PermissionError):
        vault._safe("../../../../etc/passwd")


@pytest.mark.asyncio
async def test_search_across_agents(tmp_vault_path):
    vault = ObsidianVault(vault_path=tmp_vault_path, root_folder="AI-Agents")
    provider = ObsidianMemoryProvider(vault=vault)
    await provider.remember(Memory.new("Alex", "unique-token-xyz discovered", "medium"))
    hits = await provider.search("unique-token-xyz", limit=5)
    assert any("unique-token-xyz" in m.content for m in hits)
