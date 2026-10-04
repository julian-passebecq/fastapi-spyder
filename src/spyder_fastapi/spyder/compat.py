"""Small compatibility contract for the Spyder APIs FastAPI Studio uses."""

from __future__ import annotations

from inspect import signature


def _missing_parameters(callable_obj, expected: set[str]) -> set[str]:
    try:
        parameters = set(signature(callable_obj).parameters)
    except (TypeError, ValueError):
        return set(expected)
    return expected - parameters


def spyder_contract_issues() -> list[str]:
    """Return actionable incompatibilities for the Spyder integration surface."""

    issues: list[str] = []

    try:
        from spyder.plugins.debugger.utils.breakpointsmanager import (
            BreakpointsManager,
        )
    except Exception as exc:
        issues.append(f"Debugger breakpoint manager unavailable: {exc}")
        BreakpointsManager = None

    try:
        from spyder.plugins.editor.plugin import Editor
    except Exception as exc:
        issues.append(f"Editor plugin unavailable: {exc}")
        Editor = None

    try:
        from spyder.plugins.ipythonconsole.plugin import IPythonConsole
        from spyder.plugins.ipythonconsole.widgets.shell import ShellWidget
    except Exception as exc:
        issues.append(f"IPython Console debug API unavailable: {exc}")
        IPythonConsole = None
        ShellWidget = None

    if Editor is not None:
        for name in ("load", "get_codeeditor_for_filename"):
            if not callable(getattr(Editor, name, None)):
                issues.append(f"Editor.{name} is unavailable")

    if BreakpointsManager is not None:
        for name in ("get_breakpoints", "toogle_breakpoint"):
            if not callable(getattr(BreakpointsManager, name, None)):
                issues.append(f"BreakpointsManager.{name} is unavailable")

        toggle = getattr(BreakpointsManager, "toogle_breakpoint", None)
        if callable(toggle):
            missing = _missing_parameters(toggle, {"line_number"})
            if missing:
                issues.append(
                    "BreakpointsManager.toogle_breakpoint no longer exposes "
                    "line_number"
                )

    if IPythonConsole is not None:
        if not callable(getattr(IPythonConsole, "get_current_shellwidget", None)):
            issues.append(
                "IPythonConsole.get_current_shellwidget is unavailable"
            )

        run_script = getattr(IPythonConsole, "run_script", None)
        if not callable(run_script):
            issues.append("IPythonConsole.run_script is unavailable")
        else:
            expected = {
                "filename",
                "wdir",
                "args",
                "current_client",
                "method",
            }
            missing = _missing_parameters(run_script, expected)
            if missing:
                issues.append(
                    "IPythonConsole.run_script is missing: "
                    + ", ".join(sorted(missing))
                )

    if ShellWidget is not None and not callable(
        getattr(ShellWidget, "interrupt_kernel", None)
    ):
        issues.append("ShellWidget.interrupt_kernel is unavailable")

    return issues


def variable_explorer_available() -> bool:
    """Return whether Spyder's native Variable Explorer plugin is present."""

    try:
        from spyder.api.plugins import Plugins
        from spyder.plugins.variableexplorer.plugin import VariableExplorer
    except Exception:
        return False

    return (
        getattr(Plugins, "VariableExplorer", None) is not None
        and getattr(VariableExplorer, "NAME", None)
        == Plugins.VariableExplorer
    )


__all__ = [
    "spyder_contract_issues",
    "variable_explorer_available",
]
