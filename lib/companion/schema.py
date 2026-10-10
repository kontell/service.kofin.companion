"""The schema gate, carried from Kofin: write only a database version that
was tested against a fixture of its schema.

Discovery lists ``special://database/`` and takes the newest file per
prefix. A version outside the map refuses every handler that needs that
database, and the status document names the version found. Admitting a
version is a fixture dump from an untouched install, the L2 suite green
against it, and the entry here (Kofin's ``docs/myvideos149-gate.md`` is the
worked example of the whole path).

The map opens at the floor of the API build this serves, Piers: MyVideos
149, MyMusic 84, Textures 14. Omega's versions are not carried, since no
API build runs there.

Allowed module-level state: the discovery cache. Database filenames cannot
change within a Kodi process, so a stale entry is still correct; tests reset
it through :func:`reset_cache`.
"""

import os
import re
from typing import Any, Dict, Optional, Set, Tuple

import xbmcvfs

from companion.log import Logger

LOG = Logger(__name__)

PREFIXES = {"video": "MyVideos", "music": "MyMusic", "texture": "Textures"}

SUPPORTED: Dict[str, Set[int]] = {
    "video": {149},
    "music": {84},
    "texture": {14},
}

DATABASE_DIR = "special://database/"

# Kodi's VideoAssetType::EXTRA per MyVideos version (Piers numbers VERSION 1,
# EXTRA 2). Keyed here, never inlined in a handler.
EXTRA_ITEM_TYPE: Dict[int, int] = {149: 2}

# VideoAssetTypeOwner::USER, the owner on videoversiontype rows the companion
# creates, which is what Kodi's own convert-to-version flow writes.
VIDEO_ASSET_OWNER_USER = 2

# The seeded Standard Edition row every Kodi database carries.
STANDARD_EDITION = 40400

# The chapter cache key form per Textures version: Piers wraps the request
# as ``image://video@{encoded}/?chapter={n}``.
CHAPTER_ART_WRAPPED: Dict[int, bool] = {14: True}

# Whether the seasons table carries its own plot column.
SEASON_HAS_PLOT: Dict[int, bool] = {149: True}


class SchemaError(Exception):
    """A database the companion will not write."""


class DatabaseMissing(SchemaError):
    def __init__(self, kind: str) -> None:
        super().__init__("no %s database found" % kind)
        self.kind = kind


class SchemaUnsupported(SchemaError):
    def __init__(self, kind: str, version: int) -> None:
        super().__init__("unsupported %s database v%s" % (kind, version))
        self.kind = kind
        self.version = version


_cache: Dict[str, Tuple[str, int]] = {}


def reset_cache() -> None:
    _cache.clear()


def discover(kind: str) -> Tuple[str, int]:
    """(filename, version) of the newest database file for ``kind``."""
    if kind in _cache:
        return _cache[kind]
    prefix = PREFIXES[kind]
    pattern = re.compile(r"^%s(\d+)\.db$" % prefix)
    newest = ("", 0)
    _dirs, files = xbmcvfs.listdir(DATABASE_DIR)
    for db_file in files:
        match = pattern.match(db_file)
        if match:
            version = int(match.group(1))
            if version > newest[1]:
                newest = (db_file, version)
    if not newest[0]:
        raise DatabaseMissing(kind)
    LOG.info("discovered %s database: %s", kind, newest[0])
    _cache[kind] = newest
    return newest


def check(kind: str) -> int:
    """The discovered version, gated against the map."""
    _db_file, version = discover(kind)
    if version not in SUPPORTED[kind]:
        raise SchemaUnsupported(kind, version)
    return version


def database_path(kind: str) -> str:
    """Absolute path of the gated database file for ``kind``."""
    db_file, _version = discover(kind)
    check(kind)
    return os.path.join(xbmcvfs.translatePath(DATABASE_DIR), db_file)


def gate_report() -> Dict[str, Dict[str, Any]]:
    """Per kind: the version found, whether it is served, and why not."""
    report: Dict[str, Dict[str, Any]] = {}
    for kind in PREFIXES:
        try:
            version = check(kind)
        except SchemaUnsupported as error:
            report[kind] = {
                "version": error.version,
                "ok": False,
                "reason": "unsupported",
                "supported": sorted(SUPPORTED[kind]),
            }
        except DatabaseMissing:
            report[kind] = {"version": None, "ok": False, "reason": "missing"}
        else:
            report[kind] = {"version": version, "ok": True, "reason": ""}
    return report


def gate_refusal(
    gate: Dict[str, Dict[str, Any]], needs: Tuple[str, ...]
) -> Optional[str]:
    """The reason a handler needing ``needs`` is refused, or None."""
    for kind in needs:
        entry = gate.get(kind) or {"ok": False, "reason": "missing", "version": None}
        if not entry["ok"]:
            if entry["reason"] == "missing":
                return "gate: no %s database" % PREFIXES[kind]
            return "gate: %s v%s is not served" % (PREFIXES[kind], entry["version"])
    return None
