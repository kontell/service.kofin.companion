"""``group_versions``: a movie whose version files Kodi imported as movies
of their own, grouped under the movie that owns the identity (plan C1,
ledger S24).

Kodi groups nothing from a plugin source (a plugin item's tag takes the NFO
branch of RetrieveInfoForMovie), so each version file is a movie until the
Versions Manager, or this, converts it. The conversion is Kodi's own
``CVideoDatabase::ConvertVideoToVersion`` statement for statement: the
duplicate's assets move to the owner, its movie art becomes the art of its
file as a version, the duplicate movie row goes with its path hash kept so
the scanner does not re-import it, and its version row is typed by the
source's name through Kofin's ``resolve_version_type``. The server's
default version needs no field: the owner's own file plays the server's
first source and stays Kodi's default, as the target's file does in Kodi's
flow.

Nothing is undone on withdrawal: the rows are Kodi's, a grouped version
stays grouped, and the request leaves the document when Kofin's readback no
longer lists the duplicate rows.
"""

from typing import Any, Dict, List

from companion import paths
from companion.contract import Request
from companion.db import Database
from companion.handlers.base import Context, Handler, Refused
from companion.kodidb.movies import Movies
from companion.ledger import Entry
from companion.log import Logger

LOG = Logger(__name__)


class GroupVersions(Handler):
    kind = "group_versions"
    family = "groupVersions"
    needs = ("video",)

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        owner = request.fields.get("owner") or {}
        versions = request.fields.get("versions") or []
        try:
            owner_id = int(owner["movieid"])
            duplicates = [
                (int(v["movieid"]), str(v.get("file") or ""), str(v.get("name") or ""))
                for v in versions
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise Refused("malformed request: %s" % error) from error
        if not duplicates:
            raise Refused("no versions named")
        grouped: List[Dict[str, Any]] = []
        with Database("video") as db:
            movies = Movies(db.cursor)
            if not movies.itemtype:
                raise Refused("no videoversion tables")
            owner_file = movies.movie_file(owner_id)
            if owner_file is None:
                raise Refused("owner row %d is gone" % owner_id)
            if not paths.owned(owner_file[1], context.namespace) or (
                owner.get("file") and owner_file[1] != owner["file"]
            ):
                raise Refused("not owned: owner %d" % owner_id)
            for duplicate_id, file_url, name in duplicates:
                row = movies.movie_file(duplicate_id)
                if row is None:
                    LOG.info("movie %d is already gone", duplicate_id)
                    continue
                file_id, filename = row[0], row[1]
                if not paths.owned(filename, context.namespace) or (
                    file_url and filename != file_url
                ):
                    raise Refused("not owned: version %d" % duplicate_id)
                if paths.without_source(filename) != paths.without_source(
                    owner_file[1]
                ):
                    raise Refused(
                        "version %d is not a file of movie %d"
                        % (duplicate_id, owner_id)
                    )
                moved = movies.move_assets(duplicate_id, owner_id)
                movies.convert_movie_art_to_version_art(duplicate_id, file_id)
                movies.delete_movie(duplicate_id)
                type_id = movies.resolve_version_type(name, filename)
                movies.set_version(file_id, type_id)
                grouped.append(
                    {"movieid": duplicate_id, "fileid": file_id, "type": type_id}
                )
                LOG.info(
                    "movie %d grouped into %d as %r (%d asset row(s) moved)",
                    duplicate_id,
                    owner_id,
                    name,
                    moved,
                )
            movies.sweep_orphan_version_types()
        return {"owner": owner_id, "grouped": grouped}

    def withdraw(self, entry: Entry, context: Context) -> None:
        LOG.debug(
            "grouping of movie %s stands; nothing to undo", entry.data.get("owner")
        )

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        """Still grouped: no duplicate row is back, every grouped file is a
        version of the owner."""
        owner_id = int(entry.data.get("owner") or 0)
        with Database("video", commit_close=False) as db:
            movies = Movies(db.cursor)
            for grouped in entry.data.get("grouped") or []:
                if movies.movie_exists(int(grouped["movieid"])):
                    return False
                if not movies.is_version_of(int(grouped["fileid"]), owner_id):
                    return False
        return True
