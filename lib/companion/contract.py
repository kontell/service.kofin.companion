"""The contract with Kofin: the request document, and the files the
companion writes back (Kofin's ``docs/companion-addon-plan.md`` section 3).

The document is Kofin's desired state, not a queue: a request present is
wanted, a request absent is withdrawn. ``Companion.Requests`` over
``JSONRPC.NotifyAll`` is Kofin's ping that it changed; ``Companion.Status``
is the companion's that the status document did. There is no acknowledgement
message: Kofin confirms a write by reading Kodi back, or by the content hash
in the status document where no public read exists.
"""

import hashlib
import json
import os
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

KOFIN_ID = "plugin.video.kofin"

REQUESTS_MESSAGE = "Companion.Requests"
STATUS_MESSAGE = "Companion.Status"
# The settings button asks the running service to reconcile (default.py).
WAKE_MESSAGE = "Companion.Wake"

DOCUMENT_VERSIONS = (1, 2)
STATUS_VERSION = 1
LEDGER_VERSION = 1

KOFIN_DATA = "special://profile/addon_data/" + KOFIN_ID + "/"
REQUESTS_PATH = KOFIN_DATA + "companion/requests.json"
CAPABILITIES_PATH = KOFIN_DATA + "api-capabilities.json"
OWN_DATA = "special://profile/addon_data/service.kofin.companion/"
STATUS_PATH = OWN_DATA + "status.json"
LEDGER_PATH = OWN_DATA + "written.json"


class DocumentError(Exception):
    """A document that exists and cannot be read as one."""


class Request(NamedTuple):
    id: str
    kind: str
    library: str
    item: str
    fields: Dict[str, Any]
    hash: str


class Document(NamedTuple):
    version: int
    generation: int
    namespace: str
    device: str
    kinds: Tuple[str, ...]
    requests: Dict[str, Request]


EMPTY = Document(0, 0, "", "", (), {})


def content_hash(request: Dict[str, Any]) -> str:
    """What a request asks, as a stable digest: everything but its id."""
    body = {k: v for k, v in request.items() if k != "id"}
    key = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_document(raw: Any) -> Document:
    if not isinstance(raw, dict):
        raise DocumentError("document is not an object")
    version = raw.get("v")
    if not isinstance(version, int):
        raise DocumentError("document carries no version")
    requests_raw = raw.get("requests")
    if not isinstance(requests_raw, list):
        raise DocumentError("document carries no request list")
    requests: Dict[str, Request] = {}
    for entry in requests_raw:
        if not isinstance(entry, dict):
            raise DocumentError("request is not an object")
        request_id = entry.get("id")
        kind = entry.get("kind")
        if not isinstance(request_id, str) or not request_id:
            raise DocumentError("request without an id")
        if not isinstance(kind, str) or not kind:
            raise DocumentError("request %s without a kind" % request_id)
        if request_id in requests:
            raise DocumentError("request %s listed twice" % request_id)
        fields = {k: v for k, v in entry.items() if k not in ("id", "kind")}
        requests[request_id] = Request(
            request_id,
            kind,
            str(entry.get("library") or ""),
            str(entry.get("item") or ""),
            fields,
            content_hash(entry),
        )
    kinds_raw = raw.get("kinds")
    kinds = tuple(str(k) for k in kinds_raw) if isinstance(kinds_raw, list) else ()
    return Document(
        version,
        int(raw.get("generation") or 0),
        str(raw.get("namespace") or ""),
        str(raw.get("device") or ""),
        kinds,
        requests,
    )


def read_document(path: str) -> Document:
    """The document at ``path``; the empty document when there is none.

    A document that exists and does not parse raises :class:`DocumentError`:
    nothing is withdrawn on the strength of a file that could not be read.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return EMPTY
    except (OSError, ValueError) as error:
        raise DocumentError("unreadable document: %s" % error) from error
    return parse_document(raw)


def kofin_backend(path: str) -> Optional[str]:
    """Kofin's build, from its capability report: ``api``, ``sql`` or None
    when no report exists (no Kofin, or one that never wrote it)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    backend = report.get("backend")
    return str(backend) if backend else None


def write_json(path: str, data: Dict[str, Any]) -> None:
    """Whole-file replace through a part file, so a reader never sees half."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(path + ".part", path)


def read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def decode_ping(data: str) -> Optional[Dict[str, Any]]:
    """The payload of a NotifyAll ping: a JSON object, or the list wrapping
    Kodi's builtin NotifyAll produces."""
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        payload = payload[0]
    return payload if isinstance(payload, dict) else None


def list_requests(document: Document) -> List[Request]:
    return list(document.requests.values())
