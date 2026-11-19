from __future__ import annotations

import sys

from projectflow.bridge import is_bridge_request, run_bridge
from projectflow.core.dossier_link import find_dossier_link
from projectflow.logging import configure_logging
from projectflow.reset import RESET_ARGUMENT, reset_local_data


def main() -> int:
    if is_bridge_request(sys.argv):
        # Headless request from MailFlow: no window, no single-instance hand-off.
        return run_bridge(sys.argv)
    link = find_dossier_link(sys.argv[1:])
    if link is not None:
        # A « Réf. » link clicked in a fiche: open that folder, no window, no hand-off.
        from projectflow.dossier_links import run_dossier_link  # noqa: PLC0415 - needs Qt

        return run_dossier_link(link)
    if RESET_ARGUMENT in sys.argv:
        # Installer's "Nouvelle installation": erase everything, no window.
        configure_logging()
        return 1 if reset_local_data() else 0
    from projectflow.app import run  # noqa: PLC0415 - Qt is only needed for the window

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
