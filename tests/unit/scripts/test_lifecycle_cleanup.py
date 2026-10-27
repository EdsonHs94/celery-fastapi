"""Cleanup no oculta recursos pendientes ni sustituye el fallo del recorrido."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import verify_report_lifecycle as lifecycle


@pytest.mark.parametrize(
    "outcomes",
    [
        (0, 0),
        (2, 0),
        (0, 3),
        (2, 3),
        ("timeout", 0),
    ],
)
@pytest.mark.parametrize("primary_failure", [False, True])
def test_cleanup_attempts_both_commands_and_preserves_primary_failure(
    monkeypatch, capsys, outcomes, primary_failure
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        outcome = outcomes[len(calls) - 1]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(
            returncode=outcome, stdout="", stderr="docker diagnostic"
        )

    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    original = ValueError("primary lifecycle failure") if primary_failure else None
    has_cleanup_failure = any(value != 0 for value in outcomes)

    def finish():
        try:
            if original:
                raise original
        finally:
            lifecycle.cleanup_resources(
                ["docker", "compose", "-p", "unique-project"],
                ["docker"],
                "unique-image:local",
                {"TASK_ENV": "isolated"},
                primary_error=sys.exc_info()[1],
            )

    if original:
        with pytest.raises(ValueError) as caught:
            finish()
        assert caught.value is original
    elif has_cleanup_failure:
        with pytest.raises(RuntimeError, match="Cleanup incompleto"):
            finish()
    else:
        finish()
    assert len(calls) == 2
    assert calls[0][0][-3:] == ["down", "--volumes", "--remove-orphans"]
    assert calls[1][0] == ["docker", "image", "rm", "unique-image:local"]
    assert [call[1]["timeout"] for call in calls] == [180, 60]
    assert all(call[1]["env"] == {"TASK_ENV": "isolated"} for call in calls)
    diagnostics = capsys.readouterr().err
    if has_cleanup_failure:
        assert "cleanup" in diagnostics.lower()
        if "timeout" in outcomes:
            assert "TimeoutExpired" in diagnostics
        else:
            assert "returncode=" in diagnostics and "docker diagnostic" in diagnostics
    else:
        assert "returncode=0" in diagnostics
