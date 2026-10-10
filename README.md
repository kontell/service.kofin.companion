[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)

# Kofin Companion

`service.kofin.companion` is the service add-on that closes, for the API build of [Kofin](https://github.com/kontell/plugin.video.kofin/tree/kofin-or), the parity gaps Kodi's public interfaces cannot. It reads one document Kofin writes, reconciles Kodi's databases toward it, and reports what it did in a status document Kofin folds into its capability report. Kofin never depends on it: absent, the gap stays open and is logged.

It is allowed what an official-repository add-on is not, direct access to Kodi's databases, which is why it is published on repository.kontell only and never submitted to the official repository.

The plan it is built to is `docs/companion-addon-plan.md` in the Kofin repository.
