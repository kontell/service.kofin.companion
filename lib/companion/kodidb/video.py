"""Rows the C4 handlers write: the tag link of an episode, a season's plot,
a file's stream flags, an emptied set. New code beside the carried
``movies.py``, in its shape."""

from companion.kodidb import queries as QU

# CStreamDetail::StreamType
STREAM_AUDIO = 1
STREAM_SUBTITLE = 2


class Video:
    def __init__(self, cursor):
        self.cursor = cursor

    def file_of(self, media, media_id):
        """(idFile, strFilename) of a movie, episode or music video, or None."""
        statement = {
            "movie": QU.get_movie_file,
            "episode": QU.get_episode_file,
            "musicvideo": QU.get_musicvideo_file,
        }.get(media)
        if statement is None:
            return None
        self.cursor.execute(statement, (media_id,))
        row = self.cursor.fetchone()
        return (row[0], row[1]) if row else None

    # -- tags ------------------------------------------------------------------

    def tag_id(self, name):
        """Find-or-create the tag row; Kodi's AddTag by name."""
        self.cursor.execute(QU.get_tag, (name,))
        row = self.cursor.fetchone()
        if row:
            return row[0]
        self.cursor.execute(QU.add_tag, (name,))
        return self.cursor.lastrowid

    def link_tag(self, tag_id, media_id, media_type):
        self.cursor.execute(QU.link_tag, (tag_id, media_id, media_type))

    def unlink_tag(self, tag_id, media_id, media_type):
        """Kodi's delete_tag trigger drops the tag row when nothing links it."""
        self.cursor.execute(QU.unlink_tag, (tag_id, media_id, media_type))

    def has_tag_link(self, tag_id, media_id, media_type):
        self.cursor.execute(QU.has_tag_link, (tag_id, media_id, media_type))
        return self.cursor.fetchone()[0] == 1

    # -- seasons ---------------------------------------------------------------

    def season(self, season_id):
        """(idShow, every path its show is linked to, plot) of a season, or
        None. Every path, not the first one returned: a show merged with
        another source has a row for each, in no promised order."""
        self.cursor.execute(QU.get_season_show_paths, (season_id,))
        rows = self.cursor.fetchall()
        if not rows:
            return None
        return (rows[0][0], [row[1] for row in rows if row[1]], rows[0][2])

    def set_season_plot(self, season_id, plot):
        self.cursor.execute(QU.update_season_plot, (plot, season_id))

    # -- stream flags ----------------------------------------------------------

    def stream_rows(self, file_id, stream_type):
        """[(rowid, iFlags, iSource)] of a file's streams of one type, in the
        order the scanner filed them, which is the order Kodi reads them back."""
        self.cursor.execute(QU.get_stream_rows, (file_id, stream_type))
        return self.cursor.fetchall()

    def set_stream_flags(self, rowid, flags, source):
        self.cursor.execute(QU.update_stream_flags, (flags, source, rowid))

    # -- sets ------------------------------------------------------------------

    def delete_empty_set(self, title):
        self.cursor.execute(QU.delete_empty_set, (title,))
        return self.cursor.rowcount

    def empty_set_exists(self, title):
        self.cursor.execute(QU.count_empty_set, (title,))
        return self.cursor.fetchone()[0] > 0
