"""The video metadata Kodi's public interfaces cannot write (plan C4): the
favourite tag on an episode (S14), a season's plot after its import (S18),
the flags of a file's audio and subtitle streams (S09), and the row of a
set Kofin emptied (S23).

Each is one small write to a row Kofin owns, read back before the write
through the row's resolver URL, or the show's path for a season. A tag is
undone on withdrawal, since the request leaves when the episode stops being
a favourite; a plot, a flag or a deletion stands.
"""

from typing import Any, Dict, List

from companion import paths
from companion.contract import Request
from companion.db import Database
from companion.handlers.base import Context, Handler, Refused
from companion.kodidb.video import STREAM_AUDIO, STREAM_SUBTITLE, Video
from companion.ledger import Entry
from companion.log import Logger

LOG = Logger(__name__)

FAMILY = "videoMetadata"


def _owner_id(request: Request, key: str) -> int:
    owner = request.fields.get("owner") or {}
    try:
        return int(owner[key])
    except (KeyError, TypeError, ValueError) as error:
        raise Refused("malformed request: %s" % error) from error


class EpisodeTags(Handler):
    kind = "episode_tags"
    family = FAMILY
    needs = ("video",)

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        episode_id = _owner_id(request, "episodeid")
        names = [str(t) for t in request.fields.get("tags") or [] if str(t).strip()]
        if not names:
            raise Refused("no tags named")
        links: List[List[Any]] = []
        with Database("video") as db:
            video = Video(db.cursor)
            found = video.file_of("episode", episode_id)
            if found is None:
                raise Refused("episode row %d is gone" % episode_id)
            if not paths.owned(found[1], context.namespace):
                raise Refused("not owned: episode %d" % episode_id)
            for name in names:
                tag_id = video.tag_id(name)
                video.link_tag(tag_id, episode_id, "episode")
                links.append([int(tag_id), name])
        LOG.info("episode %d tagged %s", episode_id, ", ".join(names))
        return {"episodeid": episode_id, "links": links}

    def withdraw(self, entry: Entry, context: Context) -> None:
        episode_id = int(entry.data.get("episodeid") or 0)
        with Database("video") as db:
            video = Video(db.cursor)
            for tag_id, _name in entry.data.get("links") or []:
                video.unlink_tag(int(tag_id), episode_id, "episode")
        LOG.info("episode %d untagged", episode_id)

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        episode_id = int(entry.data.get("episodeid") or 0)
        with Database("video", commit_close=False) as db:
            video = Video(db.cursor)
            for tag_id, _name in entry.data.get("links") or []:
                if not video.has_tag_link(int(tag_id), episode_id, "episode"):
                    return False
        return True


class SeasonPlot(Handler):
    kind = "season_plot"
    family = FAMILY
    needs = ("video",)

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        season_id = _owner_id(request, "seasonid")
        plot = str(request.fields.get("plot") or "")
        with Database("video") as db:
            video = Video(db.cursor)
            found = video.season(season_id)
            if found is None:
                raise Refused("season row %d is gone" % season_id)
            if not paths.owned(found[1], context.namespace):
                raise Refused("not owned: season %d" % season_id)
            if found[2] != plot:
                video.set_season_plot(season_id, plot)
                LOG.info("season %d plot updated", season_id)
        return {"seasonid": season_id}

    def withdraw(self, entry: Entry, context: Context) -> None:
        LOG.debug("plot of season %s stands", entry.data.get("seasonid"))

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        season_id = int(entry.data.get("seasonid") or 0)
        with Database("video", commit_close=False) as db:
            found = Video(db.cursor).season(season_id)
        return found is not None and found[2] == str(request.fields.get("plot") or "")


class StreamFlags(Handler):
    kind = "stream_flags"
    family = FAMILY
    needs = ("video",)

    def _wanted(self, request: Request) -> Dict[int, List[int]]:
        flags = request.fields.get("flags") or {}
        try:
            return {
                STREAM_AUDIO: [int(f) for f in flags.get("audio") or []],
                STREAM_SUBTITLE: [int(f) for f in flags.get("subtitle") or []],
            }
        except (TypeError, ValueError) as error:
            raise Refused("malformed flags: %s" % error) from error

    def _file(self, request: Request, video: Video, context: Context) -> int:
        media = str(request.fields.get("media") or "")
        key = {"movie": "movieid", "episode": "episodeid", "musicvideo": "musicvideoid"}
        if media not in key:
            raise Refused("unknown media %r" % media)
        media_id = _owner_id(request, key[media])
        found = video.file_of(media, media_id)
        if found is None:
            raise Refused("%s row %d is gone" % (media, media_id))
        if not paths.owned(found[1], context.namespace):
            raise Refused("not owned: %s %d" % (media, media_id))
        return int(found[0])

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        wanted = self._wanted(request)
        written = 0
        with Database("video") as db:
            video = Video(db.cursor)
            file_id = self._file(request, video, context)
            for stream_type, flags in wanted.items():
                rows = video.stream_rows(file_id, stream_type)
                for (rowid, current), bits in zip(rows, flags):
                    if (current or 0) != bits:
                        video.set_stream_flags(rowid, bits)
                        written += 1
        if written:
            LOG.info("file %d: %d stream flag(s) written", file_id, written)
        return {"fileid": file_id}

    def withdraw(self, entry: Entry, context: Context) -> None:
        LOG.debug("stream flags of file %s stand", entry.data.get("fileid"))

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        wanted = self._wanted(request)
        with Database("video", commit_close=False) as db:
            video = Video(db.cursor)
            try:
                file_id = self._file(request, video, context)
            except Refused:
                return False
            for stream_type, flags in wanted.items():
                rows = video.stream_rows(file_id, stream_type)
                for (_rowid, current), bits in zip(rows, flags):
                    if (current or 0) != bits:
                        return False
        return True


class RemoveSet(Handler):
    kind = "remove_set"
    family = FAMILY
    needs = ("video",)

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        title = str(request.fields.get("title") or "")
        if not title:
            raise Refused("no title")
        with Database("video") as db:
            removed = Video(db.cursor).delete_empty_set(title)
        if removed:
            LOG.info("empty set %r removed (%d row(s))", title, removed)
        return {"title": title, "removed": removed}

    def withdraw(self, entry: Entry, context: Context) -> None:
        LOG.debug("set %r stays removed", entry.data.get("title"))

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        title = str(request.fields.get("title") or "")
        with Database("video", commit_close=False) as db:
            return not Video(db.cursor).empty_set_exists(title)
