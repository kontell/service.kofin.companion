"""Kodi's own interfaces, the few the companion needs."""

import json
from typing import Any, Dict, Optional

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from companion.log import Logger

LOG = Logger(__name__)

ADDON_ID = "service.kofin.companion"

# Kofin holds its music scanner behind this window property while a pass
# writes (core/state.py); the companion stays off the database meanwhile.
KOFIN_HOLD_PROPERTY = "kofin.native.hold"
_HOME_WINDOW = 10000


class RpcError(Exception):
    pass


def rpc(method: str, params: Optional[Dict[str, Any]] = None) -> Any:
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    raw = xbmc.executeJSONRPC(json.dumps(request))
    try:
        reply = json.loads(raw)
    except ValueError as error:
        raise RpcError("%s: unparseable reply" % method) from error
    if not isinstance(reply, dict):
        raise RpcError("%s: malformed reply" % method)
    if "error" in reply:
        raise RpcError("%s: %s" % (method, reply["error"]))
    return reply.get("result")


def notify(message: str, data: Dict[str, Any]) -> None:
    """``JSONRPC.NotifyAll`` from the companion's own id."""
    try:
        rpc(
            "JSONRPC.NotifyAll",
            {"sender": ADDON_ID, "message": message, "data": data},
        )
    except RpcError as error:
        LOG.warning("notification %s not sent: %s", message, error)


def library_scanning() -> bool:
    return bool(
        xbmc.getCondVisibility("Library.IsScanningVideo")
        or xbmc.getCondVisibility("Library.IsScanningMusic")
    )


def kofin_holds() -> bool:
    """Whether Kofin's pass is holding a scanner (its music writes)."""
    window = xbmcgui.Window(_HOME_WINDOW)
    return any(
        bool(window.getProperty(KOFIN_HOLD_PROPERTY + "." + scanner))
        for scanner in ("video", "music")
    )


def addon_version(addon_id: str) -> Optional[str]:
    """The add-on's version when installed and enabled, else None."""
    try:
        reply = rpc(
            "Addons.GetAddonDetails",
            {"addonid": addon_id, "properties": ["enabled", "version"]},
        )
    except RpcError:
        return None
    addon = reply.get("addon") if isinstance(reply, dict) else None
    if not isinstance(addon, dict) or not addon.get("enabled"):
        return None
    return str(addon.get("version") or "")


def translate(path: str) -> str:
    return xbmcvfs.translatePath(path)


def setting_bool(setting_id: str) -> bool:
    try:
        return bool(xbmcaddon.Addon(ADDON_ID).getSettingBool(setting_id))
    except (RuntimeError, TypeError):
        # A setting the build does not define, or no add-on context (tests).
        return True


def own_version() -> str:
    try:
        return str(xbmcaddon.Addon(ADDON_ID).getAddonInfo("version"))
    except RuntimeError:
        return ""


def localized(string_id: int) -> str:
    try:
        return str(xbmcaddon.Addon(ADDON_ID).getLocalizedString(string_id))
    except RuntimeError:
        return ""
