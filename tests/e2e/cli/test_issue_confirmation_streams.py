"""Where the confirmation of a destructive verb is written: a real terminal, real streams.

`CliRunner` has no terminal: `sys.stdin.isatty()` is false inside it, so it can prove the refusal
and it cannot prove the question. These tests hand the child process a pseudo-terminal as
`stdin` only, keeping `stdout` and `stderr` on separate pipes, which is the only way to observe
which of the two carries the prompt.

The child is the real CLI in a fresh interpreter, the same bootstrap the concurrency tests use.
"""

from __future__ import annotations

import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLI_ENV, Cli

pytestmark = pytest.mark.slow

#: Runs the real CLI in a fresh interpreter: `app()` reads `sys.argv[1:]`.
BOOTSTRAP = "from hifa.cli.main import app; app()"

#: `src/` of this checkout: the child must import this tree, not an installed one.
SRC = Path(__file__).resolve().parents[3] / "src"

CATALOG_REFUSAL = "hifa: operation canceled"


def _run_in_a_terminal(cli: Cli, *args: str, answer: bytes | None = b"n\n") -> tuple[int, str, str]:
    """Run the CLI with a terminal on `stdin`; return `(exit_code, stdout, stderr)`.

    `answer=None` closes the terminal before the question is read, so the child sees EOF. The
    child gets `CLI_ENV` on top of this process, so its rendering does not depend on the shell
    that started the suite; `PYTHONPATH` still points at this checkout's `src/`.
    """
    master, slave = pty.openpty()
    env = {**os.environ, **CLI_ENV, "PYTHONPATH": str(SRC)}
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", BOOTSTRAP, *args],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cli.cwd),
            env=env,
        )
    finally:
        os.close(slave)
    try:
        if answer is not None:
            os.write(master, answer)
        else:
            os.close(master)
            master = -1
        out, err = proc.communicate(timeout=120)
    finally:
        if master >= 0:
            os.close(master)
    return proc.returncode, out.decode(), err.decode()


def test_the_archive_prompt_goes_to_stderr_and_stdout_stays_empty(cli: Cli) -> None:
    cli.create("Terminal archive")
    code, out, err = _run_in_a_terminal(cli, "issue", "archive", "ENG-1")
    assert code == 2
    assert out == ""
    assert "Archive ENG-1?" in err
    assert "[y/N]" in err
    assert CATALOG_REFUSAL in err
    assert cli.issue("ENG-1")["archived_at"] is None


def test_the_delete_prompt_goes_to_stderr_and_stdout_stays_empty(cli: Cli) -> None:
    cli.create("Terminal delete")
    code, out, err = _run_in_a_terminal(cli, "issue", "delete", "ENG-1")
    assert code == 2
    assert out == ""
    assert "Send ENG-1 to trash?" in err
    assert "[y/N]" in err
    assert CATALOG_REFUSAL in err
    assert cli.issue("ENG-1")["trashed"] == 0


def test_the_json_prompt_also_keeps_stdout_empty(cli: Cli) -> None:
    cli.create("Terminal json")
    code, out, err = _run_in_a_terminal(cli, "issue", "delete", "ENG-1", "--json")
    assert code == 2
    assert out == ""
    assert "Send ENG-1 to trash?" in err
    assert "confirmation_required" in err
    assert cli.issue("ENG-1")["trashed"] == 0


def test_an_answered_yes_runs_the_write_and_prints_the_row_on_stdout(cli: Cli) -> None:
    cli.create("Terminal yes")
    code, out, err = _run_in_a_terminal(cli, "issue", "delete", "ENG-1", answer=b"y\n")
    assert code == 0
    assert "ENG-1" in out
    assert "Send ENG-1 to trash?" not in out
    assert cli.issue("ENG-1")["trashed"] == 1


def test_eof_on_the_delete_prompt_refuses_without_a_write(cli: Cli) -> None:
    cli.create("Terminal eof")
    code, out, err = _run_in_a_terminal(cli, "issue", "delete", "ENG-1", answer=None)
    assert code == 2
    assert out == ""
    assert CATALOG_REFUSAL in err
    assert cli.issue("ENG-1")["trashed"] == 0
