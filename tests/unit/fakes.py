"""Stand-ins for the Kodi the companion talks to."""

import json
from typing import Any, Dict, List


class FakeKodi:
    """``xbmc.executeJSONRPC`` with the methods the companion calls, the
    notifications it sent, and the scan and hold state."""

    def __init__(self) -> None:
        self.addons: Dict[str, str] = {}
        self.notified: List[Dict[str, Any]] = []
        self.scanning = False
        self.properties: Dict[str, str] = {}
        self.settings: Dict[str, bool] = {}
        self.version = "0.1.0"

    def execute(self, raw: str) -> str:
        request = json.loads(raw)
        method = request["method"]
        params = request.get("params") or {}
        if method == "JSONRPC.NotifyAll":
            self.notified.append(params)
            return json.dumps({"id": 1, "jsonrpc": "2.0", "result": "OK"})
        if method == "Addons.GetAddonDetails":
            addon_id = params["addonid"]
            if addon_id not in self.addons:
                return json.dumps(
                    {
                        "id": 1,
                        "jsonrpc": "2.0",
                        "error": {"code": -32602, "message": "Invalid params."},
                    }
                )
            return json.dumps(
                {
                    "id": 1,
                    "jsonrpc": "2.0",
                    "result": {
                        "addon": {
                            "addonid": addon_id,
                            "enabled": True,
                            "version": self.addons[addon_id],
                        }
                    },
                }
            )
        return json.dumps(
            {
                "id": 1,
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found."},
            }
        )

    def cond_visibility(self, condition: str) -> bool:
        if condition in ("Library.IsScanningVideo", "Library.IsScanningMusic"):
            return self.scanning
        return False


class FakeWindow:
    def __init__(self, properties: Dict[str, str]) -> None:
        self._properties = properties

    def getProperty(self, key: str) -> str:
        return self._properties.get(key, "")

    def setProperty(self, key: str, value: str) -> None:
        self._properties[key] = value

    def clearProperty(self, key: str) -> None:
        self._properties.pop(key, None)


class FakeAddon:
    def __init__(self, kodi: FakeKodi, addon_id: str = "") -> None:
        self._kodi = kodi

    def getSettingBool(self, setting_id: str) -> bool:
        return self._kodi.settings.get(setting_id, True)

    def getAddonInfo(self, info: str) -> str:
        return self._kodi.version if info == "version" else ""

    def getLocalizedString(self, string_id: int) -> str:
        return "string %d" % string_id
