"""KernelShellBackend — executes Python commands inside the current IPython kernel.

Subclasses LocalShellBackend. For Python invocations (`python foo.py`,
`python -c "..."`), extracts the source and routes to
`get_ipython().run_cell(code)` so that `display()`, ipywidgets, and
plotly/matplotlib render inline in the cell. Non-Python shell commands
(pip, conda, curl, gdalwarp, etc.) and commands containing shell
operators (`|`, `>`, `&&`, etc.) pass through to the parent subprocess
implementation unchanged.

Enables a fundamentally richer class of skills: interactive widgets,
live plots, progress bars, and shared kernel state — none of which are
possible from a subprocess.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse


_SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ";;", ">", ">>", "<", "<<", "<<<"}


def _tokenize(command: str) -> list[str] | None:
    """Tokenize a shell command, separating unquoted shell operators as their own tokens."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None


def _parse_python_invocation(command: str) -> tuple[str, list[str]] | None:
    """Try to parse a command as a simple Python invocation.

    Returns (source_code, argv) where source_code is either the raw
    Python source (from `-c`) or the marker `__FILE__:<path>` signaling
    that the caller should read the script file.

    Returns None if:
        - The command has shell operators (`|`, `>`, `&&`, etc.) outside quotes
        - The command is not `python[3]` (with optional flags)
        - The command uses `-m` (module invocation — not intercepted)
    """
    tokens = _tokenize(command)
    if not tokens:
        return None

    if any(tok in _SHELL_OPERATORS for tok in tokens):
        return None

    exe = os.path.basename(tokens[0])
    if exe not in {"python", "python3"}:
        return None

    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        flag = tokens[i]
        if flag in {"-u", "-B", "-O", "-OO"}:
            i += 1
            continue
        if flag == "-c":
            if i + 1 >= len(tokens):
                return None
            source = tokens[i + 1]
            argv = ["-c", *tokens[i + 2 :]]
            return source, argv
        if flag == "-m":
            return None
        return None

    if i >= len(tokens):
        return None

    script = tokens[i]
    script_args = tokens[i + 1 :]
    return ("__FILE__:" + script, [script, *script_args])


class KernelShellBackend(LocalShellBackend):
    """LocalShellBackend variant that routes Python commands to the live IPython kernel.

    Must be instantiated from inside an IPython kernel — `get_ipython()` must
    return a non-None InteractiveShell. Otherwise falls back to parent behavior.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        try:
            from IPython import get_ipython

            self._ipython = get_ipython()
        except Exception:
            self._ipython = None

    def execute(self, command: str) -> ExecuteResponse:
        if self._ipython is None:
            return super().execute(command)

        if not command or not isinstance(command, str):
            return super().execute(command)

        parsed = _parse_python_invocation(command)
        if parsed is None:
            return super().execute(command)

        source, argv = parsed

        if source.startswith("__FILE__:"):
            script_path = source[len("__FILE__:") :]
            candidate = Path(script_path)
            if not candidate.is_absolute():
                candidate = Path(self.cwd) / candidate
            try:
                code = candidate.read_text()
            except OSError as e:
                return ExecuteResponse(
                    output=f"[stderr] Could not read script {candidate}: {e}",
                    exit_code=1,
                    truncated=False,
                )
            file_path_for_kernel = str(candidate)
        else:
            code = source
            file_path_for_kernel = "<string>"

        return self._run_in_kernel(code, argv, file_path_for_kernel)

    async def aexecute(self, command: str) -> ExecuteResponse:
        """Run on the current (main) thread so run_cell stays on the kernel's thread.

        The default protocol dispatches via asyncio.to_thread, which would call
        run_cell from a worker thread — unsafe for IPython's display hooks and
        user_ns access. Sage runs the agent from the main kernel thread inside
        an asyncio loop, so calling execute() directly here keeps run_cell on
        the main thread.
        """
        return self.execute(command)

    def _run_in_kernel(
        self, code: str, argv: list[str], file_path: str
    ) -> ExecuteResponse:
        """Execute `code` in the current IPython kernel with argv, __file__, and cwd set.

        Captures stdout/stderr as text for the agent while allowing rich displays
        (widgets, plots) to render to the notebook cell.
        """
        import sys

        from IPython.utils.capture import capture_output

        ip = self._ipython
        user_ns = ip.user_ns

        prev_argv = sys.argv
        prev_file = user_ns.get("__file__", None)
        prev_file_existed = "__file__" in user_ns
        prev_cwd = os.getcwd()

        sys.argv = argv if argv else [file_path]
        user_ns["__file__"] = file_path
        try:
            os.chdir(str(self.cwd))
        except OSError:
            pass

        try:
            with capture_output(stdout=True, stderr=True, display=False) as cap:
                result = ip.run_cell(code, store_history=False, silent=False)
        finally:
            sys.argv = prev_argv
            if prev_file_existed:
                user_ns["__file__"] = prev_file
            else:
                user_ns.pop("__file__", None)
            try:
                os.chdir(prev_cwd)
            except OSError:
                pass

        output_parts: list[str] = []
        if cap.stdout:
            output_parts.append(cap.stdout)
        if cap.stderr:
            stderr_lines = cap.stderr.strip().split("\n")
            output_parts.extend(f"[stderr] {line}" for line in stderr_lines)

        exit_code = 0
        if result.error_before_exec is not None:
            exit_code = 1
            output_parts.append(f"[stderr] SyntaxError: {result.error_before_exec}")
        elif result.error_in_exec is not None:
            exit_code = 1
            exc = result.error_in_exec
            output_parts.append(
                f"[stderr] {type(exc).__name__}: {exc}"
            )

        output = "\n".join(output_parts) if output_parts else "<no output>"

        truncated = False
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            truncated = True

        if exit_code != 0:
            output = f"{output.rstrip()}\n\nExit code: {exit_code}"

        return ExecuteResponse(
            output=output,
            exit_code=exit_code,
            truncated=truncated,
        )


__all__ = ["KernelShellBackend"]
