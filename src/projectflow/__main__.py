from __future__ import annotations

import sys

from projectflow.bridge import is_bridge_request, run_bridge


def main() -> int:
    if is_bridge_request(sys.argv):
        # Headless request from MailFlow: no window, no single-instance hand-off.
        return run_bridge(sys.argv)
    from projectflow.app import run  # noqa: PLC0415 - Qt is only needed for the window

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
