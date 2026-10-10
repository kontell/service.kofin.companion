"""Rows the way Kodi's scanner leaves them for a Kofin movie, on a fixture
database: one path row per movie folder, a files row per file (a plugin row
keeps the whole URL in strFilename), a movie row with its version row,
links and art. Shared by the handler tests."""

import sqlite3
from typing import Dict, List, Optional

NAMESPACE = "ns1"
LIB = "1" * 32
BASE = "plugin://plugin.video.kofin/native/%s/%s/movies/" % (NAMESPACE, LIB)


def movie_dir(item_id: str) -> str:
    return BASE + item_id + "/"


def movie_url(item_id: str, source: str = "") -> str:
    url = movie_dir(item_id) + "?mode=play&id=" + item_id
    return url + "&mediasourceid=" + source if source else url


def add_path(conn: sqlite3.Connection, path: str, content: str = "movies") -> int:
    cursor = conn.execute(
        "INSERT INTO path (strPath, strContent, strScraper, strHash, useFolderNames, noUpdate) "
        "VALUES (?, ?, 'metadata.local', 'hash', 1, 1)",
        (path, content),
    )
    return int(cursor.lastrowid)


def add_file(conn: sqlite3.Connection, path_id: int, url: str) -> int:
    cursor = conn.execute(
        "INSERT INTO files (idPath, strFilename, playCount, dateAdded) VALUES (?, ?, 0, '2026-01-01 00:00:00')",
        (path_id, url),
    )
    return int(cursor.lastrowid)


def add_movie(
    conn: sqlite3.Connection,
    item_id: str,
    title: str,
    source: str = "",
    path_id: Optional[int] = None,
    art: Optional[Dict[str, str]] = None,
    actors: Optional[List[str]] = None,
    streams: bool = True,
) -> Dict[str, int]:
    """A movie row as the scanner files a Kofin movie (or one of its version
    files, when ``source`` names one): its own versionversion row at the
    Standard Edition, art, a cast link, stream details and a unique id."""
    if path_id is None:
        path_id = add_path(conn, movie_dir(item_id))
    url = movie_url(item_id, source)
    file_id = add_file(conn, path_id, url)
    cursor = conn.execute(
        "INSERT INTO movie (idFile, c00, c22, premiered) VALUES (?, ?, ?, '2001-01-01')",
        (file_id, title, movie_dir(item_id)),
    )
    movie_id = int(cursor.lastrowid)
    conn.execute(
        "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (?, ?, 'movie', 1, 40400)",
        (file_id, movie_id),
    )
    for art_type, art_url in (art or {"poster": "http://art/%s.jpg" % item_id}).items():
        conn.execute(
            "INSERT INTO art (media_id, media_type, type, url) VALUES (?, 'movie', ?, ?)",
            (movie_id, art_type, art_url),
        )
    for name in actors or ["Someone"]:
        row = conn.execute(
            "SELECT actor_id FROM actor WHERE name = ?", (name,)
        ).fetchone()
        actor_id = (
            row[0]
            if row
            else int(
                conn.execute("INSERT INTO actor (name) VALUES (?)", (name,)).lastrowid
            )
        )
        conn.execute(
            "INSERT INTO actor_link (actor_id, media_id, media_type, role, cast_order) VALUES (?, ?, 'movie', 'Self', 0)",
            (actor_id, movie_id),
        )
    conn.execute(
        "INSERT INTO uniqueid (media_id, media_type, value, type) VALUES (?, 'movie', ?, 'kofin')",
        (movie_id, NAMESPACE + ":" + item_id),
    )
    if streams:
        conn.execute(
            "INSERT INTO streamdetails (idFile, iStreamType, strVideoCodec, iVideoWidth, iVideoHeight, iVideoDuration) "
            "VALUES (?, 0, 'hevc', 1920, 1080, 5400)",
            (file_id,),
        )
    return {"movieid": movie_id, "fileid": file_id, "pathid": path_id}


def query(path: str, sql: str, args=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()
