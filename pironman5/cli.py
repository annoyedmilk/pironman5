"""Command-line interface: run, status, config, version."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from typing import Any

from . import __version__
from .config import ConfigError, load_config
from .core import Core
from .logger import get_logger, setup_logging
from .metrics import snapshot
from .web import Server

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS lets a common option before the subcommand survive its absence after it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to config.json")
    common.add_argument(
        "--mock", action="store_true", default=argparse.SUPPRESS, help="run without hardware (laptop/testing)"
    )
    common.add_argument(
        "--log-level", default=argparse.SUPPRESS, choices=("debug", "info", "warning", "error")
    )

    parser = argparse.ArgumentParser(prog="pironman5", description="Pironman 5 service", parents=[common])
    parser.add_argument("--version", action="version", version=f"pironman5 {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="start the service", parents=[common])
    sub.add_parser("status", help="print a metrics snapshot and exit", parents=[common])
    sub.add_parser("version", help="print version")

    cfg = sub.add_parser("config", help="show or set config", parents=[common])
    cfg.add_argument(
        "--set",
        nargs=2,
        metavar=("SECTION.KEY", "VALUE"),
        action="append",
        default=[],
        help="set a value, e.g. --set rgb.color '#ff0000'",
    )
    return parser


def _coerce(value: str) -> Any:
    """Turn a CLI string into bool, int, float or JSON where it is one."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    for cast in (int, float, json.loads if value[:1] in ("[", "{") else str):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(snapshot(), indent=2, default=str))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    patch: dict[str, dict[str, Any]] = {}
    for dotted, raw in args.set:
        section, _, key = dotted.partition(".")
        if not key:
            raise ConfigError(f"expected SECTION.KEY, got {dotted!r}")
        patch.setdefault(section, {})[key] = _coerce(raw)
    if patch:
        cfg, changed = cfg.merged(patch)
        cfg.save()
        print(f"updated: {', '.join(changed)}")
    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.log_level:
        cfg.system.log_level = args.log_level
    setup_logging(cfg.system.log_level)

    # Write the defaults on the first run, so the config file always exists.
    if cfg.path and not cfg.path.exists():
        try:
            cfg.save()
        except OSError as exc:
            log.warning("could not write default config: %s", exc)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    core = Core(cfg, mock=args.mock)
    try:
        core.start()
        if cfg.web.enable:
            server = Server(core, cfg.web.host, cfg.web.port)
            server.start()
        core.run(stop)
    except OSError as exc:
        log.error("cannot start: %s", exc)
        return 1
    finally:
        core.stop()
    return 0


COMMANDS = {"run": cmd_run, "status": cmd_status, "config": cmd_config, "version": cmd_version}


def main(argv: list[str] | None = None) -> int:
    parsed = build_parser().parse_args(argv)
    # The SUPPRESS defaults leave absent options unset.
    args = argparse.Namespace(**{"config": None, "mock": False, "log_level": None, **vars(parsed)})
    setup_logging(args.log_level or "info")
    try:
        return COMMANDS[args.command or "run"](args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
