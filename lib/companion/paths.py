"""Kofin's resolver URLs, as far as ownership and version files need them.

Every row kofin-or owns files under ``plugin://plugin.video.kofin/native/
<namespace>/``; a movie's version file is the movie's URL with its media
source named. The spellings are Kofin's (``sync/backends/api/paths.py``)."""

from typing import Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

BASE = "plugin://plugin.video.kofin/native/"
PARAM_MEDIA_SOURCE = "mediasourceid"


def root(namespace: str) -> str:
    return BASE + namespace + "/"


def owned(url: Optional[str], namespace: str) -> bool:
    """Whether a row's file is one Kofin wrote for this namespace."""
    return bool(url) and bool(namespace) and str(url).startswith(root(namespace))


def source_of(url: Optional[str]) -> str:
    """The media source a version file's URL names, or empty."""
    return (parse_qs(urlsplit(url or "").query).get(PARAM_MEDIA_SOURCE) or [""])[0]


def without_source(url: Optional[str]) -> str:
    """A version file's URL reduced to its movie's."""
    parsed = urlsplit(url or "")
    if parsed.scheme != "plugin" or not parsed.query:
        return url or ""
    query = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name != PARAM_MEDIA_SOURCE
    ]
    return urlunsplit(parsed._replace(query=urlencode(query)))


def is_version_url(url: Optional[str]) -> bool:
    return PARAM_MEDIA_SOURCE in parse_qs(urlsplit(url or "").query)
