"""Command-line entry point.

    python scripts/vault_parser --dry-run --verbose
    python scripts/vault_parser --clean
    python scripts/vault_parser --only links
"""

from __future__ import annotations

import argparse
import sys

from . import config as config_mod
from . import run as run_mod
from .emit import EmitError
from .vault import VaultAccessError


def _force_utf8_output() -> None:
    """Vault names carry accents and Esperanto letters (`Saŝa`, `Región`).

    A default Windows console is cp1252 and raises UnicodeEncodeError on them,
    so the report -- which quotes file paths verbatim -- must not depend on the
    console's codepage.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault_parser",
        description="Parse an Obsidian vault into Astro content. The vault is read-only.",
    )
    parser.add_argument("-c", "--config", help="path to vault.config.yaml")
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="report what would happen without writing anything",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove previous output before writing (output directories only)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show full problem lists"
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        default=None,
        help="run only these modules, ignoring config (repeatable)",
    )
    parser.add_argument(
        "--also",
        metavar="NAME",
        action="append",
        default=None,
        help="enable these modules in addition to the configured set (repeatable). "
             "Use this to preview an off-by-default module: `--only` runs it alone, "
             "which for a late module means feeding it markup the earlier modules "
             "never produced.",
    )
    parser.add_argument(
        "--skip",
        metavar="NAME",
        action="append",
        default=None,
        help="disable these modules for this run (repeatable)",
    )
    parser.add_argument(
        "--list-modules",
        action="store_true",
        help="list registered modules and their state, then exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    config = config_mod.load_or_exit(args.config)

    if args.list_modules:
        return _list_modules(config)

    try:
        result = run_mod.execute(
            config,
            dry_run=args.dry_run,
            clean=args.clean,
            only=set(args.only) if args.only else None,
            skip=set(args.skip) if args.skip else None,
            also=set(args.also) if args.also else None,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (VaultAccessError, EmitError) as exc:
        # A safety guard fired. This is always a bug in the parser, never
        # something to work around -- fail loudly.
        print(f"SAFETY: {exc}", file=sys.stderr)
        return 3

    result.report.print(verbose=args.verbose)

    if result.report.notes_written == 0:
        print(
            "\nwarning: nothing was emitted. Every note is either outside a "
            "configured collection or lacks `publish: true`.\n"
            "Set `default_publish: true` in vault.config.yaml for a full preview.",
            file=sys.stderr,
        )
    return 0


def _list_modules(config) -> int:
    from .modules.registry import Pipeline

    pipeline = Pipeline.from_config(config)
    print(f"{'order':<7}{'module':<20}{'state':<10}{'kind':<8}summary")
    for module in pipeline.modules:
        state = "enabled" if module.enabled else "disabled"
        kind = "stub" if module.stub else "active"
        print(f"{module.order:<7}{module.name:<20}{state:<10}{kind:<8}{module.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
