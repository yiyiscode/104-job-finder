from __future__ import annotations

import contextlib
import logging
import sys


def use_utf8_console() -> None:
    """Windows 主控台預設 cp950,印中文與 emoji 會整個炸掉。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # 非 tty 或已被接管的串流可能不支援,那就維持原樣
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def setup_logging(level: str = "INFO") -> None:
    use_utf8_console()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s", "%H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 這兩個在 DEBUG 下會把 log 洗到看不見重點
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
