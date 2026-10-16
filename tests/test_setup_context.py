from unittest.mock import AsyncMock

import pytest

from runner import tools, warm


@pytest.mark.parametrize("exit_code", [0, 1])
async def test_setup_records_complete_output_and_failure_status(tmp_path, monkeypatch, exit_code):
    repo = tmp_path / "repo"
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/python").touch()
    log = tmp_path / "run/setup.log"
    monkeypatch.setattr(tools, "REPO", repo)
    monkeypatch.setattr(tools, "SETUP_LOG", log)
    for key, value in warm.CACHE_ENV.items():
        monkeypatch.setenv(key, value)
    toolset = tools.Toolset({
        "config": {"capabilities": ["terminal"]},
        "repository": {"setup_command": "uv sync --frozen"},
    }, AsyncMock())
    output = "dependency output\n" * 100_000 + "final diagnostic\n"
    toolset.shell = AsyncMock(return_value={"exit_code": exit_code, "output": output})
    if exit_code:
        with pytest.raises(RuntimeError, match="final diagnostic"):
            await toolset.init()
    else:
        await toolset.init()
    toolset.shell.assert_awaited_once_with("uv sync --frozen")
    assert log.read_text() == output
    assert toolset.setup["status"] == ("completed" if exit_code == 0 else "failed")
    assert toolset.setup["command"] == "uv sync --frozen"
    assert toolset.setup["exit_code"] == exit_code
    assert toolset.setup["output_path"] == str(log)
    assert toolset.setup["repository_python"] == str(repo / ".venv/bin/python")
    assert "not test evidence" in toolset.setup["scope"]


async def test_missing_setup_is_not_reported_as_completed(monkeypatch):
    for key, value in warm.CACHE_ENV.items():
        monkeypatch.setenv(key, value)
    toolset = tools.Toolset({"config": {"capabilities": ["terminal"]}, "repository": {}}, AsyncMock())
    toolset.shell = AsyncMock()
    await toolset.init()
    assert toolset.setup == {"status": "not_configured"}
    toolset.shell.assert_not_awaited()
