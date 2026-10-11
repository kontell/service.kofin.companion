"""Pristine Kodi databases from the schema dumps carried from Kofin (L2).

The dumps are Kofin's ``tests/fixtures`` files, byte for byte: MyVideos149
and MyMusic84 from a pristine LibreELEC nightly carrying Kodi 22.0-BETA2 on
a Raspberry Pi, created rather than migrated; Textures14 from a Piers
install. Their provenance and the gate documents are in the Kofin
repository (``docs/myvideos149-gate.md``). The version row is inserted here
because its number is the fixture's identity.
"""

import os
import sqlite3

FIXTURES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fixtures"))

VIDEO_VERSION = 149
MUSIC_VERSION = 84
TEXTURE_VERSION = 14


def _apply(conn: sqlite3.Connection, filename: str) -> None:
    with open(os.path.join(FIXTURES, filename), "r", encoding="utf-8") as infile:
        conn.executescript(infile.read())


def _stamp(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO version (idVersion, iCompressCount) VALUES (?, 0)", (version,)
    )


def create_video_db(path: str, version: int = VIDEO_VERSION) -> str:
    conn = sqlite3.connect(path)
    try:
        _apply(conn, "myvideos%d.sql" % version)
        _apply(conn, "myvideos%d_seed.sql" % version)
        _stamp(conn, version)
        conn.commit()
    finally:
        conn.close()
    return path


def create_music_db(path: str, version: int = MUSIC_VERSION) -> str:
    conn = sqlite3.connect(path)
    try:
        _apply(conn, "mymusic%d.sql" % version)
        _apply(conn, "mymusic%d_seed.sql" % version)
        _stamp(conn, version)
        conn.commit()
    finally:
        conn.close()
    return path


def create_texture_db(path: str, version: int = TEXTURE_VERSION) -> str:
    """Textures has no creation-time seed rows: schema plus version only."""
    conn = sqlite3.connect(path)
    try:
        _apply(conn, "textures%d.sql" % version)
        _stamp(conn, version)
        conn.commit()
    finally:
        conn.close()
    return path
