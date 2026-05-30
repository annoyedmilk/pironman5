"""Command-line interface - argparse with small per-subcommand handlers.

Subcommands:
    run       start the service (metrics + hardware + web UI)
    status    print a one-shot metrics snapshot and exit
    config    show or set config values
    version   print version
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys

from . import __version__
from .config import ConfigError, load_config
from .logger import get_logger, setup_logging

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    # Common options live on a parent parser so they are accepted both before
    # and after the subcommand (e.g. `pironman5 run --mock`). SUPPRESS keeps an
    # unspecified flag from clobbering a value given on the other side.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to config.json")
    common.add_argument("--mock", action="store_true", default=argparse.SUPPRESS,
                        help="run without hardware (laptop/testing)")
    common.add_argument("--log-level", default=argparse.SUPPRESS,
                        choices=("debug", "info", "warning", "error"))

    parser = argparse.ArgumentParser(prog="pironman5", description="Pironman 5 service", parents=[common])
    parser.add_argument("--version", action="version", version=f"pironman5 {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="start the service", parents=[common])
    sub.add_parser("status", help="print a metrics snapshot and exit", parents=[common])
    sub.add_parser("version", help="print version")

    cfg = sub.add_parser("config", help="show or set config", parents=[common])
    cfg.add_argument("--set", nargs=2, metavar=("SECTION.KEY", "VALUE"), action="append",
                     help="set a value, e.g. --set rgb.color '#ff0000'")
    return parser


def _coerce(value: str):
    """Turn a CLI string into bool/int/float/json where it makes sense."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value and value[0] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def cmd_status(args) -> int:
    from .metrics import snapshot
    print(json.dumps(snapshot(), indent=2, default=str))
    return 0


def cmd_config(args) -> int:
    cfg = load_config(args.config)
    if args.set:
        patch: dict = {}
        for dotted, raw in args.set:
            if "." not in dotted:
                print(f"error: expected SECTION.KEY, got {dotted!r}", file=sys.stderr)
                return 2
            section, key = dotted.split(".", 1)
            patch.setdefault(section, {})[key] = _coerce(raw)
        cfg.merge(patch)
        try:
            cfg.validate()
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        cfg.save()
        print(f"updated: {', '.join(patch)}")
    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def cmd_run(args) -> int:
    from .core import Core
    from .web import run_server

    cfg = load_config(args.config)
    if args.log_level:
        cfg.system.log_level = args.log_level
    setup_logging(cfg.system.log_level)

    # Write the defaults out on first run so the config file always exists.
    if cfg.path and not cfg.path.exists():
        try:
            cfg.save()
        except OSError as exc:
            log.warning("could not write default config: %s", exc)

    core = Core(cfg, mock=args.mock)

    async def _run() -> None:
        await core.start()
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                pass

        try:
            if cfg.web.enable:
                await run_server(core, cfg.web.host, cfg.web.port, stop)
            else:
                await stop.wait()
        finally:
            await core.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Fill in defaults for the SUPPRESS-backed common options when absent.
    for attr, default in (("config", None), ("mock", False), ("log_level", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    setup_logging(args.log_level or "info")

    command = args.command or "run"
    handlers = {
        "run": cmd_run,
        "status": cmd_status,
        "config": cmd_config,
        "version": lambda a: (print(__version__) or 0),
    }
    handler = handlers.get(command)
    if handler is None:
        build_parser().print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
