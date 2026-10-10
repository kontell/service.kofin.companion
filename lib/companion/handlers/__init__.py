"""The handler registry: one module per request kind, each one declaring the
kind it serves, the settings family that gates it and the databases it needs
(``base.Handler``). C0 ships the registry empty."""

from typing import Dict

from companion.handlers.base import Handler


def registry() -> Dict[str, Handler]:
    handlers: Dict[str, Handler] = {}
    return handlers
