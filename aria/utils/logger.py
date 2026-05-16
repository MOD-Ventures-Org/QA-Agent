import logging
import os
import sys
from pathlib import Path

_COLORS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"


class _ColorFormatter(logging.Formatter):
    _fmt = "%(asctime)s | {color}{bold}%(levelname)-8s{reset} | %(name)s | %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, "")
        bold  = _BOLD if record.levelname in ("ERROR", "CRITICAL") else ""
        fmt   = self._fmt.format(color=color, bold=bold, reset=_RESET)
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


class _PlainFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — colored when writing to a real terminal
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    if sys.stdout.isatty():
        console.setFormatter(_ColorFormatter())
    else:
        console.setFormatter(_PlainFormatter())
    root.addHandler(console)

    # File handler — always plain text, rotated per run
    log_dir = Path(__file__).parent.parent
    log_file = log_dir / "aria.log"
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_PlainFormatter())
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning(f"Could not open log file {log_file}: {exc}")

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "motor", "pymongo", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)
