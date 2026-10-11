"""What the companion wrote, by request id, so that a withdrawn request, a
crash and a reset converge the same way: the ledger is reconciled against
the document at every wake."""

import json
import time
from typing import Any, Dict, NamedTuple, Optional

from companion import contract


class LedgerError(Exception):
    """A ledger that exists and cannot be read as one."""


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
        self.loaded = False
        self._dirty = False

    def load(self) -> None:
        """Read the ledger. No file is an empty ledger.

        A file that is there and is not a version-1 map of entries raises
        :class:`LedgerError`, as the request document does for the same
        reason: read as empty, the next apply would save over the only
        record of the rows a withdrawal has to undo.
        """
        self.entries = {}
        self.loaded = False
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            self.loaded = True
            return
        except (OSError, ValueError) as error:
            raise LedgerError("unreadable ledger: %s" % error) from error
        if not isinstance(raw, dict) or raw.get("v") != contract.LEDGER_VERSION:
            raise LedgerError(
                "ledger is not a version %s ledger" % contract.LEDGER_VERSION
            )
        stored = raw.get("requests")
        if not isinstance(stored, dict) or not all(
            isinstance(entry, dict) for entry in stored.values()
        ):
            raise LedgerError("ledger entries are malformed")
        for request_id, entry in stored.items():
            self.entries[str(request_id)] = Entry(
                str(entry.get("kind") or ""),
                str(entry.get("hash") or ""),
                dict(entry.get("data") or {}),
                str(entry.get("at") or ""),
            )
        self.loaded = True

    def save(self) -> None:
        # Never over a ledger that was not read.
        if not self._dirty or not self.loaded:
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
