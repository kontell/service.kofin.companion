"""The statements, named as Kofin's ``sync/kodidb/queries.py`` names them."""

get_version = """
SELECT      idVersion
FROM        version
"""
get_videoversion_itemtype = """
SELECT itemType FROM videoversiontype WHERE id = ?
"""
check_video_version = """
SELECT COUNT(name) FROM sqlite_master WHERE type='table' AND name='videoversion'
"""
get_videoversiontype_by_name = """
SELECT      id
FROM        videoversiontype
WHERE       name = ? COLLATE NOCASE
AND         itemType = ?
"""
add_videoversiontype = """
INSERT INTO videoversiontype(id, name, owner, itemType)
VALUES      (NULL, ?, ?, ?)
"""
# Kodi's own statement from its v128 migration (CVideoDatabase::UpdateTables),
# which removed unused user-defined version types for the same reason this
# runs: the version picker lists every USER row, referenced or not.
delete_orphan_videoversiontypes = """
DELETE FROM videoversiontype
WHERE       id NOT IN (SELECT idType FROM videoversion)
AND         owner = ?
AND         itemType = ?
"""
update_video_version = """
UPDATE      videoversion
SET         idType = ?, itemType = ?
WHERE       idFile = ?
"""
# Kodi's UpdateAssetsOwner: every asset of the source movie moves to the
# target, the source's own file among them.
move_assets = """
UPDATE      videoversion
SET         idMedia = ?
WHERE       idMedia = ?
AND         media_type = 'movie'
"""
get_movie_file = """
SELECT      movie.idFile, files.strFilename, path.strPath, path.idPath
FROM        movie
JOIN        files ON files.idFile = movie.idFile
JOIN        path ON path.idPath = files.idPath
WHERE       movie.idMovie = ?
"""
get_file = """
SELECT      files.strFilename, path.strPath
FROM        files
JOIN        path ON path.idPath = files.idPath
WHERE       files.idFile = ?
"""
get_art = """
SELECT      type, url
FROM        art
WHERE       media_id = ?
AND         media_type = ?
"""
get_art_id = """
SELECT      art_id
FROM        art
WHERE       media_id = ?
AND         media_type = ?
AND         type = ?
"""
add_art = """
INSERT INTO art(media_id, media_type, type, url)
VALUES      (?, ?, ?, ?)
"""
update_art_url = """
UPDATE      art
SET         url = ?
WHERE       art_id = ?
"""
delete_movie = """
DELETE FROM movie
WHERE       idMovie = ?
"""
count_versions_of = """
SELECT      COUNT(*)
FROM        videoversion
WHERE       idMedia = ?
AND         media_type = 'movie'
AND         idFile = ?
"""
movie_exists = """
SELECT      COUNT(*)
FROM        movie
WHERE       idMovie = ?
"""
# A movie's asset rows beside its own file: version files and extras, with
# the type each is filed under.
get_assets_of_movie = """
SELECT      videoversion.idFile, files.strFilename, videoversion.itemType,
            videoversion.idType
FROM        videoversion
JOIN        files ON files.idFile = videoversion.idFile
WHERE       videoversion.idMedia = ?
AND         videoversion.media_type = 'movie'
AND         videoversion.idFile != ?
"""
delete_file = """
DELETE FROM files
WHERE       idFile = ?
"""
