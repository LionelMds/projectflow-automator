from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from projectflow.exceptions import PrintError
from projectflow.platform import mac_printing
from projectflow.platform.mac_printing import print_with_excel_for_mac


class FakeMac:
    """Records the commands; each AppleScript step answers from ``answers``."""

    def __init__(self, **answers: tuple[int, str]) -> None:
        self.answers = answers
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        step = self._step(list(command))
        code, output = self.answers.get(step, (0, ""))
        return subprocess.CompletedProcess(list(command), code, output, "refus" if code else "")

    @staticmethod
    def _step(command: list[str]) -> str:
        if command[0] == "open":
            return "open"
        script = command[2]
        steps = {
            mac_printing.OPEN_BOOKS_SCRIPT: "books",
            mac_printing.WAIT_AND_FIT_SCRIPT: "fit",
            mac_printing.A4_SCRIPT: "a4",
            mac_printing.SELECT_PRINTER_SCRIPT: "printer",
            mac_printing.PRINT_SCRIPT: "print",
        }
        return steps[script]

    def step_args(self, step: str) -> list[str]:
        return next(command[3:] for command in self.commands if self._step(command) == step)


@pytest.fixture
def fiche(tmp_path: Path) -> Path:
    path = tmp_path / "2026-5245 Fiche.xlsx"
    path.write_bytes(b"xlsx")
    return path


def test_prints_on_one_a4_page_and_closes_the_fiche_it_opened(fiche: Path) -> None:
    mac = FakeMac(printer=(0, "Xerox Bac2"))

    used = print_with_excel_for_mac(fiche, printer_name="Xerox_Bac2", copies=2, run=mac)

    assert used == "Xerox Bac2"
    assert mac.commands[1] == ["open", "-a", "Microsoft Excel", str(fiche)]
    assert mac.step_args("fit") == [fiche.name, str(mac_printing.OPEN_WAIT_SECONDS)]
    assert mac.step_args("printer") == ["Xerox_Bac2", "Xerox Bac2"]
    assert mac.step_args("print") == [fiche.name, "2", "close"]


def test_a_fiche_already_open_in_excel_stays_open(fiche: Path) -> None:
    mac = FakeMac(books=(0, f"Autre.xlsx\n{fiche.name}"), printer=(0, "Xerox"))

    print_with_excel_for_mac(fiche, printer_name="Xerox", run=mac)

    assert mac.step_args("print") == [fiche.name, "1", "keep"]


def test_optional_steps_never_stop_the_printing(fiche: Path) -> None:
    # Older Excel versions may refuse the A4 paper or the printer change.
    mac = FakeMac(a4=(1, ""), printer=(1, ""))

    used = print_with_excel_for_mac(fiche, printer_name="Xerox", run=mac)

    assert used == "imprimante actuelle d'Excel"
    assert any(command[2] == mac_printing.PRINT_SCRIPT for command in mac.commands[2:])


@pytest.mark.parametrize("failing", ["fit", "print"])
def test_failed_printing_leaves_the_fiche_open_for_cmd_p(fiche: Path, failing: str) -> None:
    mac = FakeMac(**{failing: (1, "")})

    with pytest.raises(PrintError, match="Cmd\\+P"):
        print_with_excel_for_mac(fiche, printer_name="Xerox", run=mac)


def test_missing_excel_or_fiche_is_reported(fiche: Path, tmp_path: Path) -> None:
    with pytest.raises(PrintError, match="Microsoft Excel est requis"):
        print_with_excel_for_mac(fiche, printer_name="Xerox", run=FakeMac(open=(1, "")))

    with pytest.raises(PrintError, match="Fiche introuvable"):
        print_with_excel_for_mac(tmp_path / "absente.xlsx", printer_name="Xerox", run=FakeMac())


def test_unresponsive_excel_is_reported(fiche: Path) -> None:
    def hanging(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(command), mac_printing.STEP_TIMEOUT_SECONDS)

    with pytest.raises(PrintError, match="ne repond pas"):
        print_with_excel_for_mac(fiche, printer_name="Xerox", run=hanging)
