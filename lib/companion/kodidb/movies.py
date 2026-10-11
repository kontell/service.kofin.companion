"""Movie rows: what the version handlers need of Kofin's ``kodidb/movies.py``."""

from sqlite3 import DatabaseError

from companion import schema
from companion.kodidb import queries as QU
from companion.log import Logger

LOG = Logger(__name__)


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

    def resolve_version_type(self, name):
        """Map a version's name to a VERSION ``videoversiontype`` id.

        Empty or ``Standard Edition`` is the seeded Standard Edition
        (40400); anything else is found or created under the VERSION
        itemType, Kodi's builtins first.

        Kofin's rule that a name equal to the source file's stem is no label
        (its audit finding A4-1: a type row per single-file movie put 1,799
        entries in the Versions picker) is applied before the request is
        published: ``metadata.version_label`` compares the name with
        ``MediaSource.Path`` and sends an empty one. It cannot be applied
        here, where the only file name at hand is a plugin URL.
        """
        name = (name or "").strip()
        if not name or name.lower() == "standard edition":
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

    def file_ids(self, filename):
        """The files rows stored under a URL: a plugin row keeps its whole
        URL in ``strFilename``."""
        self.cursor.execute(QU.get_file_ids_by_filename, (filename,))
        return [row[0] for row in self.cursor.fetchall()]

    def version_row(self, file_id):
        """(idMedia, media_type, idType) of a file's asset row, or None."""
        self.cursor.execute(QU.get_version_row, (file_id,))
        return self.cursor.fetchone()

    def attach_version(self, file_id, movie_id, type_id):
        """Make a file a version of a movie, whatever its asset row said."""
        self.cursor.execute(
            QU.attach_version, (movie_id, self.itemtype, type_id, file_id)
        )
        if not self.cursor.rowcount:
            self.cursor.execute(
                QU.add_video_version, (file_id, movie_id, self.itemtype, type_id)
            )

    def is_version_of(self, file_id, movie_id):
        self.cursor.execute(QU.count_versions_of, (movie_id, file_id))
        return self.cursor.fetchone()[0] == 1

    def movie_exists(self, movie_id):
        self.cursor.execute(QU.movie_exists, (movie_id,))
        return self.cursor.fetchone()[0] == 1

    # -- assets (versions and extras) of a grouped movie ---------------------

    def assets_of(self, movie_id):
        """[(idFile, strFilename, itemType, idType, strPath)] of every asset
        row of the movie: its version files, its extras, and its own file,
        whichever of them is the default at the moment."""
        self.cursor.execute(QU.get_assets_of_movie, (movie_id,))
        return self.cursor.fetchall()

    def set_default_file(self, movie_id, old_file_id, file_id, base_path):
        """Kodi's SetDefaultVideoVersion, statement for statement: the
        movie plays ``file_id``, and the art changes hands with it."""
        self.cursor.execute(QU.set_default_file, (file_id, base_path, movie_id))
        self.cursor.execute(QU.movie_art_to_version, (old_file_id, movie_id))
        self.cursor.execute(QU.version_art_to_movie, (movie_id, file_id))

    def delete_file(self, file_id):
        """Drop the files row; Kodi's delete_file trigger cascades the
        videoversion, bookmark, settings, streamdetails and art rows."""
        self.cursor.execute(QU.delete_file, (file_id,))
