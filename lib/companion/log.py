"""Logging through xbmc.log with the same masking Kofin applies.

Nothing the companion handles should carry a credential, since it never
talks to the server, but a request document echoes Kofin's resolver URLs
and a transcode URL carries ``api_key=``; the patterns stay for that.
"""

import re
import traceback
from typing import Any, List, Tuple

import xbmc

REDACTED = "***"

_PATTERNS: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(r"(api_key=)[^&\"'\s]+", re.I), r"\g<1>" + REDACTED),
    (re.compile(r'(Token=")[^"]*(")'), r"\g<1>" + REDACTED + r"\g<2>"),
    (
        re.compile(r'("(?:AccessToken|Pw|Password|Secret)"\s*:\s*")[^"]*(")'),
        r"\g<1>" + REDACTED + r"\g<2>",
    ),
]


def mask(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class Logger:
    def __init__(self, name: str) -> None:
        self._prefix = "[companion] %s: " % name

    def debug(self, msg: object, *args: Any) -> None:
        self._write(xbmc.LOGDEBUG, msg, args)

    def info(self, msg: object, *args: Any) -> None:
        self._write(xbmc.LOGINFO, msg, args)

    def warning(self, msg: object, *args: Any) -> None:
        self._write(xbmc.LOGWARNING, msg, args)

    def error(self, msg: object, *args: Any) -> None:
        self._write(xbmc.LOGERROR, msg, args)

    def exception(self, msg: object, *args: Any) -> None:
        self._write(xbmc.LOGERROR, msg, args, trailer=traceback.format_exc())

    def _write(
        self, level: int, msg: object, args: Tuple[Any, ...], trailer: str = ""
    ) -> None:
        text = str(msg)
        if args:
            try:
                text = text % args
            except (TypeError, ValueError):
                text = " ".join([text] + [str(a) for a in args])
        if trailer:
            text = text + "\n" + trailer
        xbmc.log(self._prefix + mask(text), level)
