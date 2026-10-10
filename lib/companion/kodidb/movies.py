"""Movie rows: what the version handlers need of Kofin's ``kodidb/movies.py``."""

from sqlite3 import DatabaseError

from companion import schema
from companion.kodidb import queries as QU
from companion.log import Logger

LOG = Logger(__name__)


def _file_stem(path):
    """The file name without its extension, for either path separator --
    Jellyfin reports the server's own path, which may be a Windows one."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


class Movies:
    """Version types and the grouping statements, on one cursor."""

    itemtype: int

    def __init__(self, cursor):
        self.cursor = cursor
        try:
            self.cursor.execute(QU.get_videoversion_itemtype, [schema.STANDARD_EDITION])
            self.itemtype = self.cursor.fetchone()[0]
        except (IndexError, DatabaseError, TypeError) as e:
            LOG.warning("Unable to fetch videoversion itemtype: %s", e)
            self.itemtype = 0

    def get_extra_type_id(self, name, item_type):
        """Find-or-create the named videoversiontype row (owner USER, like
        Kodi's own convert-to-extra flow). Matches builtins first via name
        + itemType (e.g. Director's Cut at 40407)."""
        self.cursor.execute(QU.get_videoversiontype_by_name, (name, item_type))
        row = self.cursor.fetchone()
        if row:
            return row[0]
        self.cursor.execute(
            QU.add_videoversiontype,
            (name, schema.VIDEO_ASSET_OWNER_USER, item_type),
        )
        return self.cursor.lastrowid

    def resolve_version_type(self, name, path=None):
        """Map ``MediaSource.Name`` to a VERSION ``videoversiontype`` id.

        Empty / ``Standard Edition`` -> the seeded Standard Edition (40400).
        A name equal to the source file's own stem is Jellyfin's default for
        a film with no other versions (``Video.GetMediaSourceName`` returns
        the file name without extension unless local alternate versions
        exist, in which case the folder-name prefix is stripped and a
        suffix label remains) -- that is no label at all, and it maps to the
        Standard Edition too. Otherwise find-or-create under VERSION itemType
        (builtins first). Kofin's audit finding A4-1 is why: a type row per
        single-file movie put 1,799 entries in the Versions picker.
        """
        name = (name or "").strip()
        if not name or name.lower() == "standard edition":
            return schema.STANDARD_EDITION
        if path and name.lower() == _file_stem(path).lower():
            return schema.STANDARD_EDITION
        return self.get_extra_type_id(name, self.itemtype)

    def sweep_orphan_version_types(self):
        """Drop USER-owned VERSION type rows nothing references any more:
        nothing of Kodi's removes them but its v128/v131 migrations, and the
        picker lists them all."""
        self.cursor.execute(QU.check_video_version)
        if self.cursor.fetchone()[0] == 1:
            self.cursor.execute(
                QU.delete_orphan_videoversiontypes,
                (schema.VIDEO_ASSET_OWNER_USER, self.itemtype),
            )

    # -- grouping (Kodi's ConvertVideoToVersion, statement for statement) ---

    def movie_file(self, movie_id):
        """(idFile, strFilename, strPath, idPath) of a movie's own file, or None."""
        self.cursor.execute(QU.get_movie_file, (movie_id,))
        return self.cursor.fetchone()

    def move_assets(self, source_movie_id, target_movie_id):
        """UpdateAssetsOwner: the source's versions and extras, its own file
        among them, now belong to the target."""
        self.cursor.execute(QU.move_assets, (target_movie_id, source_movie_id))
        return self.cursor.rowcount

    def convert_movie_art_to_version_art(self, movie_id, file_id):
        """SetVideoVersionDefaultArt: the movie's art becomes the art of
        its file as a version (SetArtForItem's update-or-insert)."""
        self.cursor.execute(QU.get_art, (movie_id, "movie"))
        for art_type, url in self.cursor.fetchall():
            self.set_art(file_id, "videoversion", art_type, url)

    def set_art(self, media_id, media_type, art_type, url):
        self.cursor.execute(QU.get_art_id, (media_id, media_type, art_type))
        row = self.cursor.fetchone()
        if row:
            self.cursor.execute(QU.update_art_url, (url, row[0]))
        else:
            self.cursor.execute(QU.add_art, (media_id, media_type, art_type, url))

    def delete_movie(self, movie_id):
        """The movie row alone. Kodi's delete_movie trigger takes the links,
        the art, the ratings and the unique ids, and the version row only
        while it still points at this movie -- so the move comes first.
        Deviation from DeleteMovie(ALL_ASSETS): the file's stream details
        are kept, as Kodi keeps them for a Bluray source, because a
        plugin stream can no more be re-read than a disc."""
        self.cursor.execute(QU.delete_movie, (movie_id,))

    def set_version(self, file_id, type_id):
        self.cursor.execute(QU.update_video_version, (type_id, self.itemtype, file_id))

    def is_version_of(self, file_id, movie_id):
        self.cursor.execute(QU.count_versions_of, (movie_id, file_id))
        return self.cursor.fetchone()[0] == 1

    def movie_exists(self, movie_id):
        self.cursor.execute(QU.movie_exists, (movie_id,))
        return self.cursor.fetchone()[0] == 1

    # -- assets (versions and extras) of a grouped movie ---------------------

    def assets_of(self, movie_id, own_file_id):
        """[(idFile, strFilename, itemType, idType)] of the movie's version
        files and extras, its own file left out."""
        self.cursor.execute(QU.get_assets_of_movie, (movie_id, own_file_id))
        return self.cursor.fetchall()

    def delete_file(self, file_id):
        """Drop the files row; Kodi's delete_file trigger cascades the
        videoversion, bookmark, settings, streamdetails and art rows."""
        self.cursor.execute(QU.delete_file, (file_id,))
