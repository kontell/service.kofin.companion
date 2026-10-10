"""``chapter_art``: the server's chapter images under the keys Kodi's
chapters dialog asks for, while a Kofin item plays (plan section 6, ledger
S35 and A03).

Kofin stages the images in its own profile and names the exact dynpath of
the play; the key per chapter is this handler's (``kodidb/texture.py``, per
Textures schema), the file is copied into ``Thumbnails/`` at the CRC path
and the row pair written. A withdrawn request removes rows and files; a
start sweeps rows carrying the document's device id that no request names,
which is what a crash leaves. A key that does not carry the device id is
not Kofin's and is never touched.
"""

import os
import shutil
from typing import Any, Dict, List, Tuple

from companion import kodi, schema
from companion.contract import Request
from companion.db import Database
from companion.handlers.base import Context, Handler, Refused
from companion.kodidb.texture import TextureCache, cached_rel_path, chapter_art_key
from companion.ledger import Entry
from companion.log import Logger

LOG = Logger(__name__)

THUMBNAILS = "special://thumbnails/"


def thumbnails_dir() -> str:
    return kodi.translate(THUMBNAILS)


class ChapterArt(Handler):
    kind = "chapter_art"
    family = "chapterArt"
    needs = ("texture",)

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        dynpath = str(request.fields.get("dynpath") or "")
        chapters = request.fields.get("chapters") or []
        if not dynpath or not isinstance(chapters, list) or not chapters:
            raise Refused("no chapters")
        if not context.device or ("deviceId=" + context.device) not in dynpath:
            raise Refused("not owned: the stream is not this device's")
        wrapped = schema.CHAPTER_ART_WRAPPED.get(context.version("texture"))
        if wrapped is None:
            raise Refused(
                "gate: no chapter key form for Textures v%s"
                % context.version("texture")
            )
        directory = thumbnails_dir()
        keys: List[Tuple[str, str]] = []
        with Database("texture") as db:
            cache = TextureCache(db.cursor)
            for chapter in chapters:
                try:
                    number = int(chapter["n"])
                    source = str(chapter["file"])
                    width = int(chapter.get("width") or 0)
                    height = int(chapter.get("height") or 0)
                except (KeyError, TypeError, ValueError) as error:
                    raise Refused("malformed chapter: %s" % error) from error
                if not os.path.isfile(source):
                    LOG.debug("chapter %d image missing at %s", number, source)
                    continue
                key = chapter_art_key(dynpath, number, wrapped)
                rel = cached_rel_path(key)
                target = os.path.join(directory, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target)
                cache.add(key, rel, width, height)
                keys.append((key, rel))
        if not keys:
            raise Refused("no chapter image could be read")
        LOG.info("chapter art %s: %d tile(s) seeded", request.item, len(keys))
        return {"item": request.item, "keys": [list(pair) for pair in keys]}

    def withdraw(self, entry: Entry, context: Context) -> None:
        keys = entry.data.get("keys") or []
        directory = thumbnails_dir()
        with Database("texture") as db:
            cache = TextureCache(db.cursor)
            for key, rel in keys:
                cache.remove(key)
                _remove_file(os.path.join(directory, rel))
        LOG.info(
            "chapter art %s: %d tile(s) removed", entry.data.get("item"), len(keys)
        )

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        directory = thumbnails_dir()
        with Database("texture", commit_close=False) as db:
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
        removed = 0
        directory = thumbnails_dir()
        with Database("texture") as db:
            cache = TextureCache(db.cursor)
            for pattern in (
                "chapter://%%%s%%" % context.device,
                "image://video@%%%s%%" % context.device,
            ):
                db.cursor.execute(
                    "SELECT url, cachedurl FROM texture WHERE url LIKE ?", (pattern,)
                )
                for url, cachedurl in db.cursor.fetchall():
                    if url in known:
                        continue
                    cache.remove(url)
                    _remove_file(os.path.join(directory, cachedurl))
                    removed += 1
        if removed:
            LOG.info("swept %d stale chapter tile(s)", removed)
        return removed


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        LOG.debug("could not remove %s: %s", path, error)
