"""``retire_assets``: a movie's version files and extras, exactly the ones
the server still has (plan C3, ledger S25).

Kodi's scan of a movie folder adds and never removes, and no public call
removes or renames a version or an extra, so a file the server dropped
stays a row until Clean and a renamed source keeps its old type. Kofin
names, from the mapping it acknowledged, the asset files a movie still has
and the name of each version's source; this handler types each version by
that name, deletes every asset row the request no longer names (the
delete_file trigger cascades the version row, the stream details, the art
and the bookmarks) and sweeps the version types nothing references. The
movie's own file is never touched, whichever version the user made the
default.

Nothing is undone on withdrawal: a removed row was the server's to drop.
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


def _desired(request: Request) -> Dict[str, Dict[str, Any]]:
    """{file url: {"name": source name} or {}} for every asset kept."""
    desired: Dict[str, Dict[str, Any]] = {}
    for version in request.fields.get("versions") or []:
        if not isinstance(version, dict) or not version.get("file"):
            raise Refused("malformed version")
        desired[str(version["file"])] = {"name": str(version.get("name") or "")}
    for extra in request.fields.get("extras") or []:
        if not isinstance(extra, dict) or not extra.get("file"):
            raise Refused("malformed extra")
        desired[str(extra["file"])] = {}
    return desired


class RetireAssets(Handler):
    kind = "retire_assets"
    family = "assetLifecycle"
    needs = ("video",)

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        owner = request.fields.get("owner") or {}
        try:
            owner_id = int(owner["movieid"])
        except (KeyError, TypeError, ValueError) as error:
            raise Refused("malformed request: %s" % error) from error
        desired = _desired(request)
        removed: List[Dict[str, Any]] = []
        retyped: List[Dict[str, Any]] = []
        with Database("video") as db:
            movies = Movies(db.cursor)
            if not movies.itemtype:
                raise Refused("no videoversion tables")
            own = movies.movie_file(owner_id)
            if own is None:
                raise Refused("owner row %d is gone" % owner_id)
            if not paths.owned(own[1], context.namespace):
                raise Refused("not owned: owner %d" % owner_id)
            for file_id, filename, item_type, type_id in movies.assets_of(
                owner_id, own[0]
            ):
                if not paths.owned(filename, context.namespace):
                    # Not Kofin's row: the user's own version file, left alone.
                    continue
                wanted = desired.get(filename)
                if wanted is None:
                    movies.delete_file(file_id)
                    removed.append({"fileid": file_id, "file": filename})
                    continue
                if item_type == movies.itemtype and "name" in wanted:
                    resolved = movies.resolve_version_type(wanted["name"], filename)
                    if resolved != type_id:
                        movies.set_version(file_id, resolved)
                        retyped.append(
                            {"fileid": file_id, "file": filename, "type": resolved}
                        )
            if removed or retyped:
                movies.sweep_orphan_version_types()
        if removed or retyped:
            LOG.info(
                "movie %d assets: %d removed, %d retyped",
                owner_id,
                len(removed),
                len(retyped),
            )
        return {"owner": owner_id, "removed": removed, "retyped": retyped}

    def withdraw(self, entry: Entry, context: Context) -> None:
        LOG.debug("assets of movie %s stand; nothing to undo", entry.data.get("owner"))

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        """Still exactly the desired set: no undesired row of Kofin's, every
        version at the type its name resolves to."""
        owner_id = int(entry.data.get("owner") or 0)
        desired = _desired(request)
        with Database("video", commit_close=False) as db:
            movies = Movies(db.cursor)
            own = movies.movie_file(owner_id)
            if own is None:
                return False
            for _file_id, filename, item_type, type_id in movies.assets_of(
                owner_id, own[0]
            ):
                if not paths.owned(filename, context.namespace):
                    continue
                wanted = desired.get(filename)
                if wanted is None:
                    return False
                if item_type == movies.itemtype and "name" in wanted:
                    if movies.resolve_version_type(wanted["name"], filename) != type_id:
                        return False
        return True
