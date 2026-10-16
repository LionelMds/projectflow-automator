"""Print a fiche with Microsoft Excel for Mac, driven by AppleScript.

Excel for Mac is sandboxed: it opens the fiche through Launch Services, as a
double-click would, so it never asks for access to the project folder. Each step is
its own small script, so an optional one (A4 paper, printer choice) cannot stop the
printing itself.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from projectflow.exceptions import PrintError
from projectflow.logging import get_logger

EXCEL_APP = "Microsoft Excel"
# Excel may have to start before the fiche appears among its workbooks.
OPEN_WAIT_SECONDS = 45
STEP_TIMEOUT_SECONDS = 120

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

OPEN_BOOKS_SCRIPT = """
if application "Microsoft Excel" is running then
    tell application "Microsoft Excel"
        set bookNames to name of every workbook
    end tell
    set AppleScript's text item delimiters to linefeed
    return bookNames as text
end if
return ""
"""

# Fit the sheet on one page, as on Windows. Without zoom off, Excel ignores fit to pages.
WAIT_AND_FIT_SCRIPT = """
on run argv
    set ficheName to item 1 of argv
    set waitSteps to (item 2 of argv as integer) * 2
    tell application "Microsoft Excel"
        set ficheBook to missing value
        repeat waitSteps times
            try
                set ficheBook to workbook ficheName
                exit repeat
            end try
            delay 0.5
        end repeat
        if ficheBook is missing value then error "Excel n'a pas ouvert la fiche."
        set ficheSetup to page setup object of active sheet of ficheBook
        try
            set zoom of ficheSetup to false
            set fit to pages wide of ficheSetup to 1
            set fit to pages tall of ficheSetup to 1
        end try
    end tell
end run
"""

A4_SCRIPT = """
on run argv
    tell application "Microsoft Excel"
        set paper size of page setup object of active sheet of workbook (item 1 of argv) to paper a4
    end tell
end run
"""

SELECT_PRINTER_SCRIPT = """
on run argv
    tell application "Microsoft Excel"
        repeat with candidate in argv
            try
                set active printer to (contents of candidate)
                exit repeat
            end try
        end repeat
        return active printer
    end tell
end run
"""

PRINT_SCRIPT = """
on run argv
    set ficheName to item 1 of argv
    set copiesCount to item 2 of argv as integer
    tell application "Microsoft Excel"
        set ficheBook to workbook ficheName
        print out active sheet of ficheBook copies copiesCount
        if item 3 of argv is "close" then
            delay 1
            close ficheBook saving no
        end if
    end tell
end run
"""


def print_with_excel_for_mac(
    workbook_path: Path,
    *,
    printer_name: str,
    copies: int = 1,
    run: Runner | None = None,
) -> str:
    """Print the fiche's active sheet on one A4 page; return the printer Excel used."""
    runner = run or _run
    if not workbook_path.exists():
        raise PrintError(f"Fiche introuvable: {workbook_path}")
    name = workbook_path.name
    already_open = name in _open_workbooks(runner)
    opened = _call(runner, ["open", "-a", EXCEL_APP, str(workbook_path)])
    if opened.returncode != 0:
        raise PrintError("Microsoft Excel est requis pour imprimer la fiche.")
    manual = (
        "La fiche est ouverte dans Excel : imprimez-la avec Cmd+P "
        "(Mise en page : A4, ajuster a 1 page)."
    )
    if _osascript(runner, WAIT_AND_FIT_SCRIPT, name, str(OPEN_WAIT_SECONDS)) is None:
        raise PrintError(f"Excel n'a pas pu preparer l'impression. {manual}")
    _osascript(runner, A4_SCRIPT, name)
    # Excel for Mac may refuse to change printer: it then prints on its current one,
    # and the name it reports back is the one shown to the user.
    used_printer = _osascript(runner, SELECT_PRINTER_SCRIPT, *_printer_candidates(printer_name))
    keep_open = "keep" if already_open else "close"
    if _osascript(runner, PRINT_SCRIPT, name, str(max(1, copies)), keep_open) is None:
        raise PrintError(f"Excel n'a pas pu imprimer la fiche. {manual}")
    return used_printer or "imprimante actuelle d'Excel"


def _printer_candidates(printer_name: str) -> list[str]:
    # macOS lists CUPS queue names, where spaces of the displayed name became "_".
    return list(
        dict.fromkeys(name for name in (printer_name, printer_name.replace("_", " ")) if name)
    )


def _open_workbooks(runner: Runner) -> set[str]:
    output = _osascript(runner, OPEN_BOOKS_SCRIPT)
    return {line.strip() for line in (output or "").splitlines() if line.strip()}


def _osascript(runner: Runner, script: str, *args: str) -> str | None:
    """Run one step; None when AppleScript or Excel refused it."""
    result = _call(runner, ["osascript", "-e", script, *args])
    if result.returncode != 0:
        get_logger(__name__).warning("print.mac.step_failed", error=result.stderr.strip()[:300])
        return None
    return result.stdout.strip()


def _call(runner: Runner, command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return runner(command)
    except FileNotFoundError as exc:
        raise PrintError(f"{command[0]} est introuvable sur ce Mac.") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrintError("Excel ne repond pas pour l'impression de la fiche.") from exc


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=STEP_TIMEOUT_SECONDS,
    )
