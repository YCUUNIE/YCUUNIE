"""Test fixtures. Point the vault + DB at temp dirs before any app import."""
import os
import tempfile
from pathlib import Path

import pytest

# Use an isolated temp vault so tests never touch the real one.
_TMP = Path(tempfile.mkdtemp(prefix="aiworld-test-"))
os.environ["OBSIDIAN_VAULT_PATH"] = str(_TMP / "vault")


@pytest.fixture
def tmp_vault_path():
    return _TMP / "vault"
