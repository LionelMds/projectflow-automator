r"""Copy this poste's local planning into the OneDrive planning folder (not packaged).

Run from the repository root with the system Python (``py`` on Windows); the script adds
``src`` to ``sys.path`` itself::

    py tools/planning_poc/copy_to_onedrive.py --dry-run
    py tools/planning_poc/copy_to_onedrive.py
    py tools/planning_poc/copy_to_onedrive.py --folder "ProjectFlow Planning" --dry-run

The source is the local planning store of this poste's ``planning.json`` (its
``local_directory``, else the default folder under the application data); the target is
the OneDrive folder of the account signed in to ProjectFlow on this poste (``--folder``,
else the poste's ``onedrive_folder``), with the répertoire's connection
(Files.ReadWrite.All): no new sign-in, no new consent. Nothing in OneDrive is replaced:
documents already there are counted as « deja presents » and left as they are, so the
copy can be run again. The poste's settings are not changed.

Exit codes: 0 done (or nothing to copy), 1 error (Microsoft, sign-in, settings),
3 copied with failures.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from projectflow.config import AppConfig
from projectflow.core.planning.copy import CopyReport, copy_planning
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.store import PlanningStore
from projectflow.exceptions import ProjectFlowError
from projectflow.services import create_planning_store

EXIT_ERROR = 1
EXIT_FAILURES = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="compter sans rien ecrire")
    parser.add_argument("--folder", default="", help="dossier OneDrive du planning")
    parser.add_argument("--config", type=Path, default=None, help="planning.json a utiliser")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> CopyReport:
    poste = PlanningPosteConfig.load(args.config)
    source_config = poste.model_copy(deep=True)
    source_config.backend = "local"
    target_config = poste.model_copy(deep=True)
    target_config.backend = "onedrive"
    if args.folder:
        target_config.onedrive_folder = args.folder
    print(f"Source : planning local {source_config.local_store_directory()}")
    print(f"Cible  : OneDrive / {target_config.onedrive_folder}")
    stores: list[PlanningStore] = []
    try:
        stores.append(await create_planning_store(AppConfig(), source_config))
        stores.append(await create_planning_store(AppConfig(), target_config))
        source, target = stores
        return await copy_planning(source, target, dry_run=args.dry_run)
    finally:
        for store in stores:
            await store.aclose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(run(args))
    except (ProjectFlowError, OSError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return EXIT_ERROR
    label = "a copier (essai, rien n'est ecrit)" if report.dry_run else "copies"
    print(f"{report.created} {label}")
    print(f"{report.skipped_existing} deja presents (non remplaces)")
    print(f"{report.failed} en echec")
    for error in report.errors:
        print(f"  - {error}")
    return EXIT_FAILURES if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
