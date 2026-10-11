[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)

# Kofin Companion

`service.kofin.companion` is the service add-on that closes, for the API build of [Kofin](https://github.com/kontell/plugin.video.kofin/tree/kofin-or), the parity gaps Kodi's public interfaces cannot. It reads one document Kofin writes, reconciles Kodi's databases toward it, and reports what it did in a status document Kofin folds into its capability report. Kofin never depends on it: absent, the gap stays open and is logged.

It is allowed what an official-repository add-on is not, direct access to Kodi's databases, which is why it is published on repository.kontell only and never submitted to the official repository.

The plan it is built to is `docs/companion-addon-plan.md` in the Kofin repository.

## What it serves

| Request kind | What it writes | Ledger row |
|---|---|---|
| `group_versions` | A movie's version files Kodi imported as movies of their own, grouped under the owner the way the Versions Manager does | S24 |
| `retire_assets` | A movie's version files and extras kept to exactly the server's: dropped ones removed, renamed sources retyped | S25 |
| `chapter_art` | The server's chapter images under the keys the chapters dialog asks for, while a Kofin item plays | S35, A03 |
| `episode_tags` | The favourite tag on an episode, undone when the episode stops being one | S14 |
| `season_plot` | A season's plot after its import, which Kodi sets only on insert | S18 |
| `stream_flags` | The default, forced and hearing-impaired bits on a file's stream rows | S09 |
| `remove_set` | The row of a set Kofin emptied, by title, without a clean of the whole root | S23 |

Every write is to a row Kofin owns, read back before the write; nothing is written while a scan runs; a withdrawn request is undone where the write was per play or per state and stands where it edited Kodi's own row. The status document under the add-on's profile data says what was done, what waits and what was refused, and why.

## Journal mode

Besides the requests, the companion switches the Kodi databases its gate serves to SQLite's WAL journal mode, once, at the first reconcile that finds the library idle (`lib/companion/journal.py`). Kodi keeps whatever mode a database file was left in and creates its own as rollback journals, where every statement of a library setter is a journal file written, synced and removed; Kofin's SQL build has always switched them, and the API build may not open them at all. On a Raspberry Pi 3 with its databases on an SD card the difference for a first music import was `AudioLibrary.SetSongDetails` at 17 ms a call instead of 133 ms, `SetArtistDetails` at 14 ms instead of 148 ms, and the scan itself a fifth shorter. The mode is reported under `journal` in the status document.

## Name indexes

Kodi's scanner finds every cast member, director and writer of an item with `select actor_id from actor where name like '<name>'` (`CVideoDatabase::AddActor`), and every tag and studio with the same statement on its table (`AddToTable`). LIKE ignores case, the unique index on `actor.name` does not, and `tag` and `studio` have no index on the name, so each lookup is a scan of the table and an import is quadratic in its cast. The companion adds `ix_kofin_actor_nocase`, `ix_kofin_tag_nocase` and `ix_kofin_studio_nocase` (`name COLLATE NOCASE`) beside Kodi's indexes at the first idle reconcile (`lib/companion/indexes.py`), and reports them under `indexes` in the status document. On a Raspberry Pi 3, against a library of 1,788 movies and 4,300 episodes, Kodi's statements went from 28.2 ms to 0.15 ms on `actor` (38,284 rows), 6.1 ms to 0.18 ms on `tag` and 2.3 ms to 0.21 ms on `studio`, returning the same rows. A Kodi schema upgrade drops every index, so they are asked for again at each start.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/unit -q      # L1 units and L2 against the schema fixtures
.venv/bin/python -m black --check --diff .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
tools/dev-install.sh [--flatpak]              # stage the tree into a local Kodi and bounce the service
tools/build.py [OUTDIR]                       # the install zip (default ./dist)
```

The schema gate (`lib/companion/schema.py`) serves MyVideos 149, MyMusic 84 and Textures 14; admitting a version is a fixture dump of its schema under `tests/fixtures/`, the suite green against it, and the map entry. `tools/check_kodi_schema.py` watches upstream for a newer one.

Releases: bump `addon.xml`, prepend `changelog.txt`, merge, tag `vX.Y.Z`; the workflow drafts the release and it is published by hand.
