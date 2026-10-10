"""What the companion wrote, by request id, so that a withdrawn request, a
crash and a reset converge the same way: the ledger is reconciled against
the document at every wake."""

import time
from typing import Any, Dict, NamedTuple, Optional

from companion import contract


class Entry(NamedTuple):
    kind: str
    hash: str
    data: Dict[str, Any]
    at: str


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


class Ledger:
    def __init__(self, path: str) -> None:
        self.path = path
        self.entries: Dict[str, Entry] = {}
        self._dirty = False

    def load(self) -> None:
        self.entries = {}
        raw = contract.read_json(self.path)
        if raw is None or raw.get("v") != contract.LEDGER_VERSION:
            return
        stored = raw.get("requests")
        if not isinstance(stored, dict):
            return
        for request_id, entry in stored.items():
            if not isinstance(entry, dict):
                continue
            self.entries[str(request_id)] = Entry(
                str(entry.get("kind") or ""),
                str(entry.get("hash") or ""),
                dict(entry.get("data") or {}),
                str(entry.get("at") or ""),
            )

    def save(self) -> None:
        if not self._dirty:
            return
        contract.write_json(
            self.path,
            {
                "v": contract.LEDGER_VERSION,
                "requests": {
                    request_id: {
                        "kind": e.kind,
                        "hash": e.hash,
                        "data": e.data,
                        "at": e.at,
                    }
                    for request_id, e in sorted(self.entries.items())
                },
            },
        )
        self._dirty = False

    def get(self, request_id: str) -> Optional[Entry]:
        return self.entries.get(request_id)

    def put(self, request_id: str, kind: str, hash: str, data: Dict[str, Any]) -> None:
        self.entries[request_id] = Entry(kind, hash, data, _now())
        self._dirty = True

    def remove(self, request_id: str) -> None:
        if request_id in self.entries:
            del self.entries[request_id]
            self._dirty = True
