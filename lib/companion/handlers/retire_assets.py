"""``retire_assets``: a movie's version files and extras, exactly the ones
the server still has (plan C3, ledger S25).

Kodi's scan of a movie folder adds and never removes, and no public call
removes or renames a version or an extra, so a file the server dropped
stays a row until Clean and a renamed source keeps its old type. Kofin
names, from the mapping it acknowledged, the asset files a movie still has
and the name of each version's source; this handler types each version by
that name, deletes every asset row the request no longer names (the
delete_file trigger cascades the version row, the stream details, the art
and the bookmarks) and sweeps the version types nothing references.

Which row is "the movie's own file" is decided by its URL, never by
``movie.idFile``. Kodi's Set as Default only moves ``movie.idFile``; the
file that was the default stays an asset row like any other. Kofin does
not list the movie's own file among the assets (it is the movie, not an
asset of it), so judged by ``movie.idFile`` the own file became an
undesired row the moment the user chose another default, and was deleted
with nothing to bring it back: the folder's listing had not changed, so
no scan would re-file it. Every asset row of the movie is therefore
classified once, the same way for ``apply`` and ``confirm``:

- the own file is the row of Kofin's whose URL names no media source and
  is not under ``extras/``. It is never deleted, and a movie without one
  is not served;
- a version or extra of Kofin's the request does not name is deleted. If
  it is the default at the time, the movie is first given its own file
  back the way Kodi's Set as Default would;
- a version the request names is typed by its name, the default included;
- a row that is not Kofin's (a file the user attached) is left alone,
  also when it is the default.

Nothing is undone on withdrawal: a removed row was the server's to drop.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

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


Row = Tuple[int, str, int, int, str]


def _classify(
    rows: Sequence[Row], namespace: str, version_type: int
) -> Tuple[Optional[Row], List[Row]]:
    """(the movie's own file, Kofin's other asset rows) of a movie's asset
    rows. Rows that are not Kofin's are in neither."""
    own: Optional[Row] = None
    assets: List[Row] = []
    for row in rows:
        _file_id, filename, item_type, _type_id, _path = row
        if not paths.owned(filename, namespace):
            continue
        if (
            item_type == version_type
            and not paths.is_version_url(filename)
            and not paths.is_extra_url(filename)
        ):
            # A second row by the movie's own URL is a duplicate identity,
            # which is not this handler's to settle: never an asset.
            own = own or row
            continue
        assets.append(row)
    return own, assets


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
            default = movies.movie_file(owner_id)
            if default is None:
                raise Refused("owner row %d is gone" % owner_id)
            own, assets = _classify(
                movies.assets_of(owner_id), context.namespace, movies.itemtype
            )
            if own is None:
                raise Refused("not owned: owner %d has no file of Kofin's" % owner_id)
            default_file = default[0]
            for file_id, filename, item_type, type_id, _path in assets:
                wanted = desired.get(filename)
                if wanted is None:
                    if file_id == default_file:
                        movies.set_default_file(owner_id, file_id, own[0], own[4])
                        default_file = own[0]
                        LOG.info(
                            "movie %d plays its own file again: its default"
                            " was a file the server dropped",
                            owner_id,
                        )
                    movies.delete_file(file_id)
                    removed.append({"fileid": file_id, "file": filename})
                    continue
                if item_type == movies.itemtype and "name" in wanted:
                    resolved = movies.resolve_version_type(wanted["name"])
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
        """Still a movie of this namespace with its own file, no undesired
        row of Kofin's, and every named version at the type its name
        resolves to. A named asset that is not filed is not a failure here:
        filing is the scanner's, and nothing ``apply`` does would add it."""
        owner_id = int(entry.data.get("owner") or 0)
        desired = _desired(request)
        with Database("video", commit_close=False) as db:
            movies = Movies(db.cursor)
            if movies.movie_file(owner_id) is None:
                return False
            own, assets = _classify(
                movies.assets_of(owner_id), context.namespace, movies.itemtype
            )
            if own is None:
                return False
            for _file_id, filename, item_type, type_id, _path in assets:
                wanted = desired.get(filename)
                if wanted is None:
                    return False
                if item_type == movies.itemtype and "name" in wanted:
                    if movies.resolve_version_type(wanted["name"]) != type_id:
                        return False
        return True
