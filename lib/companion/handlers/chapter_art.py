"""``chapter_art``: the server's chapter images under the keys Kodi's
chapters dialog asks for, while a Kofin item plays (plan section 6, ledger
S35 and A03).

Kofin stages the images in its own profile and names the exact dynpath of
the play; the key per chapter is this handler's (``kodidb/texture.py``, per
Textures schema), the file is copied into ``Thumbnails/`` at the CRC path
and the row pair written. A withdrawn request removes rows and files, and
so does a request that comes back changed, before its new form is applied
(the reconciler withdraws the earlier form). Once per process, rows
carrying the document's device id that no ledger entry names are swept,
which is what a crash leaves. A key that does not carry the device id is
not Kofin's and is never touched.

A file in ``Thumbnails/`` without its row is invisible to all of that: the
sweep finds files through rows. So every chapter is read before anything
is copied, the rows go in one short transaction after the files exist,
and a call that does not commit takes the files it copied with it.

Kodi names a cache file by the CRC of the lower-cased key, so two keys
that differ by case alone are two rows and one file. A file is therefore
removed only when no row names it any more.
"""

import contextlib
import os
import shutil
import sqlite3
from typing import Any, Dict, Iterator, List, Tuple

from companion import kodi, schema
from companion.contract import Request
from companion.db import Database, locked
from companion.handlers.base import Context, Handler, Refused, Retry
from companion.kodidb.texture import (
    TextureCache,
    cached_rel_path,
    chapter_art_key,
    dynpath_device,
    key_dynpath,
)
from companion.ledger import Entry
from companion.log import Logger

LOG = Logger(__name__)

THUMBNAILS = "special://thumbnails/"


def thumbnails_dir() -> str:
    return kodi.translate(THUMBNAILS)


# How long a statement waits for the texture database. Kodi's image cache
# holds it for a row at a time; whatever holds it longer is not waited out
# on the thread that serves every other request and hears the abort. The
# request is waiting then, and tried again at the next wake.
BUSY_WAIT = 5.0


@contextlib.contextmanager
def _textures(commit_close: bool = True) -> Iterator[Database]:
    try:
        with Database("texture", commit_close, BUSY_WAIT) as db:
            yield db
    except sqlite3.OperationalError as error:
        if locked(error):
            raise Retry("texture database busy") from error
        raise


class ChapterArt(Handler):
    kind = "chapter_art"
    family = "chapterArt"
    needs = ("texture",)
    # The chapters dialog is opened while the item plays, and what is not
    # in the texture database when it opens is a blank tile until it is
    # closed and opened again. The request used to be served after every
    # standing request of the pass, each one a database opened to confirm
    # it, and not at all while a scan ran: thirty seconds later, or when
    # the scan ended. It is first because of ``urgent``; it is not held back
    # by a scan because it needs the texture database and Thumbnails/ alone,
    # which Kodi's image cache writes at any moment, scan or no scan.
    urgent = True

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        dynpath = str(request.fields.get("dynpath") or "")
        chapters = request.fields.get("chapters") or []
        if not dynpath or not isinstance(chapters, list) or not chapters:
            raise Refused("no chapters")
        if not context.device or dynpath_device(dynpath) != context.device:
            raise Refused("not owned: the stream is not this device's")
        wrapped = schema.CHAPTER_ART_WRAPPED.get(context.version("texture"))
        if wrapped is None:
            raise Refused(
                "gate: no chapter key form for Textures v%s"
                % context.version("texture")
            )
        parsed: List[Tuple[int, str, int, int]] = []
        for chapter in chapters:
            try:
                parsed.append(
                    (
                        int(chapter["n"]),
                        str(chapter["file"]),
                        int(chapter.get("width") or 0),
                        int(chapter.get("height") or 0),
                    )
                )
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise Refused("malformed chapter: %s" % error) from error
        directory = thumbnails_dir()
        tiles: List[Tuple[str, str, int, int]] = []
        copied: List[str] = []
        try:
            for number, source, width, height in parsed:
                if not os.path.isfile(source):
                    LOG.debug("chapter %d image missing at %s", number, source)
                    continue
                key = chapter_art_key(dynpath, number, wrapped)
                rel = cached_rel_path(key)
                target = os.path.join(directory, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                fresh = not os.path.exists(target)
                shutil.copyfile(source, target)
                if fresh:
                    copied.append(target)
                tiles.append((key, rel, width, height))
            if not tiles:
                raise Refused("no chapter image could be read")
            # The row is the publish step: after its file, and all of them in
            # one transaction that holds the database no longer than it takes.
            with _textures() as db:
                cache = TextureCache(db.cursor)
                for key, rel, width, height in tiles:
                    cache.add(key, rel, width, height)
        except BaseException:
            for target in copied:
                _remove_file(target)
            raise
        LOG.info("chapter art %s: %d tile(s) seeded", request.item, len(tiles))
        return {
            "item": request.item,
            "keys": [[key, rel] for key, rel, _width, _height in tiles],
        }

    def withdraw(self, entry: Entry, context: Context) -> None:
        keys = entry.data.get("keys") or []
        directory = thumbnails_dir()
        with _textures() as db:
            cache = TextureCache(db.cursor)
            for key, rel in keys:
                cache.remove(key)
                if not cache.references(rel):
                    _remove_file(os.path.join(directory, rel))
        LOG.info(
            "chapter art %s: %d tile(s) removed", entry.data.get("item"), len(keys)
        )

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        directory = thumbnails_dir()
        with _textures(commit_close=False) as db:
            cache = TextureCache(db.cursor)
            for key, rel in entry.data.get("keys") or []:
                if cache.get(key) != rel or not os.path.isfile(
                    os.path.join(directory, rel)
                ):
                    return False
        return True

    def sweep(self, context: Context, entries: List[Entry]) -> int:
        """Rows carrying Kofin's device id that no ledger entry names: what
        a crash left. Every key of ours contains the device id, so nothing
        else can match; a key of Kodi's own extraction never does."""
        if not context.device:
            return 0
        known = {
            key for entry in entries for key, _rel in (entry.data.get("keys") or [])
        }
        device = context.device

        def ours(url: str) -> bool:
            # The device id as a whole value of the key's own stream URL: a
            # LIKE on it would take tv-10's rows for tv-1, and read a "%" or
            # "_" in an id as a wildcard.
            return dynpath_device(key_dynpath(url)) == device

        removed: List[Tuple[str, str]] = []
        with _textures() as db:
            cache = TextureCache(db.cursor)
            for pattern in ("chapter://%", "image://video@%"):
                removed.extend(cache.remove_like(pattern, keep=known, only=ours))
            # A file a kept row still names stays (keys that differ by case
            # alone share one).
            orphaned = sorted(
                {
                    cachedurl
                    for _url, cachedurl in removed
                    if not cache.references(cachedurl)
                }
            )
        directory = thumbnails_dir()
        for cachedurl in orphaned:
            _remove_file(os.path.join(directory, cachedurl))
        if removed:
            LOG.info("swept %d stale chapter tile(s)", len(removed))
        return len(removed)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        LOG.debug("could not remove %s: %s", path, error)
