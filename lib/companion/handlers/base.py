"""What every request kind implements."""

from typing import Any, Dict, Tuple

from companion.contract import Request
from companion.ledger import Entry


class Refused(Exception):
    """The request cannot be served as asked; Kofin reads the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Retry(Exception):
    """Not now (a lock, a scan): tried again at the next wake."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Context:
    """What a handler is given beside the request: the document's identity
    and a place to log. Handlers open databases themselves (``db.Database``),
    so a test can point a kind at a fixture through ``db.set_path_override``."""

    def __init__(self, namespace: str, device: str, gate: Dict[str, Dict[str, Any]]):
        self.namespace = namespace
        self.device = device
        self.gate = gate

    def version(self, kind: str) -> int:
        entry = self.gate.get(kind) or {}
        return int(entry.get("version") or 0)


class Handler:
    kind: str = ""
    # The settings toggle that gates the family this kind belongs to.
    family: str = ""
    # The database kinds the handler writes; each must pass the gate.
    needs: Tuple[str, ...] = ()

    def apply(self, request: Request, context: Context) -> Dict[str, Any]:
        """Write the request; return what the ledger keeps for its withdrawal."""
        raise NotImplementedError

    def withdraw(self, entry: Entry, context: Context) -> None:
        """Undo what ``apply`` recorded, as far as the rows still exist."""
        raise NotImplementedError

    def confirm(self, request: Request, entry: Entry, context: Context) -> bool:
        """Whether an applied request still holds; False re-applies it."""
        return True
