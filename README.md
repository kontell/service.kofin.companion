[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)

# Kofin Companion

`service.kofin.companion` is the service add-on that closes, for the API build of [Kofin](https://github.com/kontell/plugin.video.kofin/tree/kofin-or), the parity gaps Kodi's public interfaces cannot. It reads one document Kofin writes, reconciles Kodi's databases toward it, and reports what it did in a status document Kofin folds into its capability report. Kofin never depends on it: absent, the gap stays open and is logged.

It is allowed what an official-repository add-on is not, direct access to Kodi's databases, which is why it is published on repository.kontell only and never submitted to the official repository.

The plan it is built to is `docs/companion-addon-plan.md` in the Kofin repository.

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
