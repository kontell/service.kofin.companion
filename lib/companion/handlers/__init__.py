"""The handler registry: one module per request kind, each one declaring the
kind it serves, the settings family that gates it and the databases it needs
(``base.Handler``)."""

from typing import Dict

from companion.handlers.base import Handler
from companion.handlers.group_versions import GroupVersions


def registry() -> Dict[str, Handler]:
    handlers: Dict[str, Handler] = {}
    for handler in (GroupVersions(),):
        handlers[handler.kind] = handler
    return handlers
