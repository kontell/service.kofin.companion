"""One reconcile: the ledger against the document, every outcome into the
status document (plan section 3.3 and 3.6)."""

import time
from typing import Any, Callable, Dict, Optional, Tuple

from companion import contract, kodi, schema, upkeep
from companion.contract import Document, DocumentError, Request
from companion.handlers.base import Context, Handler, Refused, Retry
from companion.ledger import Entry, Ledger, LedgerError
from companion.log import Logger

LOG = Logger(__name__)

DONE = "done"
WAITING = "waiting"
REFUSED = "refused"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


class Reconciler:
    def __init__(
        self,
        handlers: Dict[str, Handler],
        ledger: Ledger,
        requests_path: str,
        capabilities_path: str,
        status_path: str,
        setting: Callable[[str], bool] = kodi.setting_bool,
        busy: Callable[[], bool] = lambda: kodi.library_scanning()
        or kodi.kofin_holds(),
        version: Callable[[], str] = kodi.own_version,
    ) -> None:
        self.handlers = handlers
        self.ledger = ledger
        self.requests_path = requests_path
        self.capabilities_path = capabilities_path
        self.status_path = status_path
        self.setting = setting
        self.busy = busy
        self.version = version
        self._last_idle: Optional[str] = None
        # Whether the handlers' leftovers have been swept in this process.
        self._swept = False
        # kind -> (the database version tended, what was found): see _tend.
        self._upkeep: Dict[str, Tuple[int, upkeep.Upkeep]] = {}

    # -- the run ----------------------------------------------------------------

    def run(self, trigger: str) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "v": contract.STATUS_VERSION,
            "companion": self.version(),
            "contract": list(contract.DOCUMENT_VERSIONS),
            "kinds": sorted(self.handlers),
            "trigger": trigger,
            "updated": _now(),
            "generation": None,
            "requests": {},
        }
        backend = contract.kofin_backend(self.capabilities_path)
        if backend != "api":
            report["idle"] = (
                "kofin's API build is not installed"
                if backend is None
                else "kofin's %s build writes its own rows" % backend
            )
            self._idle(report)
            return report
        if not self.ledger.loaded:
            try:
                self.ledger.load()
            except LedgerError as error:
                # Nothing is applied or withdrawn against a ledger that could
                # not be read, and it is not saved over; asked again each run.
                report["idle"] = str(error)
                LOG.warning("ledger not read: %s", error)
                self._idle(report)
                return report
        try:
            document = contract.read_document(self.requests_path)
        except DocumentError as error:
            report["idle"] = str(error)
            LOG.warning("requests document not read: %s", error)
            self._idle(report)
            return report
        if (
            document is not contract.EMPTY
            and document.version not in contract.DOCUMENT_VERSIONS
        ):
            report["idle"] = "document version %s is not served" % document.version
            LOG.warning("%s", report["idle"])
            self._idle(report)
            return report
        self._last_idle = None
        report["generation"] = document.generation
        schema.reset_cache()
        gate = schema.gate_report()
        report["gate"] = gate
        busy = self.busy()
        report["busy"] = busy
        self._tend(gate, busy)
        report["journal"] = {
            kind: found.journal or found.state
            for kind, (_version, found) in sorted(self._upkeep.items())
        }
        report["indexes"] = {
            kind: list(found.indexes)
            for kind, (_version, found) in sorted(self._upkeep.items())
            if upkeep.INDEXES.get(kind)
        }
        report["upkeep"] = {
            kind: found.state
            for kind, (_version, found) in sorted(self._upkeep.items())
        }
        context = Context(document.namespace, document.device, gate)
        outcomes: Dict[str, Dict[str, str]] = {}
        if not self._swept and context.device:
            swept = self._sweep(context)
            if swept is not None:
                report["swept"] = swept
                self._swept = True
        self._withdraw_stale(document, context, busy, outcomes)
        for request in contract.list_requests(document):
            outcomes[request.id] = self._serve(request, context, busy)
        self.ledger.save()
        report["requests"] = outcomes
        report["summary"] = {
            state: sum(1 for o in outcomes.values() if o["state"] == state)
            for state in (DONE, WAITING, REFUSED)
        }
        self._publish(report)
        return report

    # -- the steps --------------------------------------------------------------

    def _tend(self, gate: Dict[str, Dict[str, Any]], busy: bool) -> None:
        """Each served database once: WAL and its indexes (``upkeep.tend``).

        Once per database and version, not once per process: a database the
        gate did not serve at the first run (Kodi had not created it yet)
        is tended when it appears, and one Kodi has upgraded is tended
        again, since an upgrade copies the file and drops every index. A
        database that was busy is asked again; one that failed is not, and
        said so once. Never while the library is scanning."""
        for kind in schema.PREFIXES:
            entry = gate.get(kind) or {}
            if not entry.get("ok"):
                self._upkeep.pop(kind, None)
                continue
            version = int(entry["version"])
            known = self._upkeep.get(kind)
            if (
                known is not None
                and known[0] == version
                and known[1].state != upkeep.BUSY
            ):
                continue
            if busy:
                continue
            self._upkeep[kind] = (version, upkeep.tend(kind))

    def _sweep(self, context: Context) -> Optional[Dict[str, int]]:
        """Each handler's leftovers from a crash, once per process: at the
        first wake whose document names the device, since a sweep is keyed
        by it and a companion can start before Kofin has published. Not
        held back by a library scan, which writes other databases than the
        ones a sweep cleans. None when a handler's sweep failed, and then
        the next wake tries again."""
        swept: Dict[str, int] = {}
        complete = True
        for kind, handler in self.handlers.items():
            if schema.gate_refusal(context.gate, handler.needs) is not None:
                continue
            entries = [e for e in self.ledger.entries.values() if e.kind == kind]
            try:
                swept[kind] = handler.sweep(context, entries)
            except Exception:
                LOG.exception("sweep of %s failed", kind)
                complete = False
        return swept if complete else None

    def _withdraw_stale(
        self,
        document: Document,
        context: Context,
        busy: bool,
        outcomes: Dict[str, Dict[str, str]],
    ) -> None:
        for request_id, entry in list(self.ledger.entries.items()):
            if request_id in document.requests:
                continue
            handler = self.handlers.get(entry.kind)
            if handler is None:
                LOG.warning(
                    "ledger holds %s of kind %s, which no handler serves; kept",
                    request_id,
                    entry.kind,
                )
                continue
            if busy:
                outcomes[request_id] = _outcome(
                    WAITING, "withdrawal waits for the scan", entry.hash
                )
                continue
            kept = self._withdraw(request_id, entry, handler, context)
            if kept is not None:
                outcomes[request_id] = kept

    def _withdraw(
        self, request_id: str, entry: Entry, handler: Handler, context: Context
    ) -> Optional[Dict[str, str]]:
        """Undo a ledger entry and forget it. None when it is gone; else the
        outcome that says why it was kept. The entry leaves the ledger only
        after its handler has undone it: it is the one record of those rows.

        The gate is asked first, as it is before a write. A database this
        build does not serve cannot be opened, and a withdrawal that failed
        on it for that reason would be retried every thirty seconds for a
        condition only a new build clears."""
        refusal = schema.gate_refusal(context.gate, handler.needs)
        if refusal is not None:
            return _outcome(REFUSED, "withdrawal: %s" % refusal, entry.hash)
        try:
            handler.withdraw(entry, context)
        except Retry as error:
            return _outcome(WAITING, error.reason, entry.hash)
        except Exception:
            LOG.exception("withdrawal of %s (%s) failed", request_id, entry.kind)
            return _outcome(WAITING, "withdrawal failed", entry.hash)
        LOG.info("withdrew %s (%s)", request_id, entry.kind)
        self.ledger.remove(request_id)
        return None

    def _serve(self, request: Request, context: Context, busy: bool) -> Dict[str, str]:
        handler = self.handlers.get(request.kind)
        if handler is None:
            return _outcome(REFUSED, "unsupported kind", request.hash)
        if handler.family and not self.setting(handler.family):
            return _outcome(REFUSED, "disabled: %s" % handler.family, request.hash)
        refusal = schema.gate_refusal(context.gate, handler.needs)
        if refusal is not None:
            return _outcome(REFUSED, refusal, request.hash)
        entry = self.ledger.get(request.id)
        if entry is not None and entry.hash == request.hash:
            try:
                if handler.confirm(request, entry, context):
                    return _outcome(DONE, "", request.hash)
            except Exception:
                LOG.exception(
                    "confirmation of %s (%s) failed", request.id, request.kind
                )
                return _outcome(WAITING, "confirmation failed", request.hash)
            LOG.info("%s (%s) no longer holds; applied again", request.id, request.kind)
        if busy:
            return _outcome(WAITING, "scan", request.hash)
        if entry is not None and entry.hash != request.hash:
            # The request's content moved. What its earlier form wrote is
            # undone before the new form is written, so a row the request no
            # longer names does not stay behind under an entry that has
            # stopped listing it.
            kept = self._withdraw(request.id, entry, handler, context)
            if kept is not None:
                return dict(kept, hash=request.hash)
            entry = None
        try:
            data = handler.apply(request, context)
        except Refused as error:
            if entry is not None:
                # Applied before, no longer holding, and now refused: the
                # earlier write is undone before its record goes.
                kept = self._withdraw(request.id, entry, handler, context)
                if kept is not None:
                    return dict(kept, hash=request.hash)
            LOG.info("refused %s (%s): %s", request.id, request.kind, error.reason)
            return _outcome(REFUSED, error.reason, request.hash)
        except Retry as error:
            return _outcome(WAITING, error.reason, request.hash)
        except Exception:
            LOG.exception("%s (%s) failed", request.id, request.kind)
            return _outcome(WAITING, "failed; retried at the next wake", request.hash)
        self.ledger.put(request.id, request.kind, request.hash, data)
        LOG.info("applied %s (%s)", request.id, request.kind)
        return _outcome(DONE, "", request.hash)

    # -- the status document ----------------------------------------------------

    def _idle(self, report: Dict[str, Any]) -> None:
        # An idle status is written once per reason, not once per wake.
        if self._last_idle == report["idle"]:
            return
        self._last_idle = str(report["idle"])
        LOG.info("idle: %s", report["idle"])
        self._publish(report)

    def _publish(self, report: Dict[str, Any]) -> None:
        contract.write_json(self.status_path, report)
        kodi.notify(
            contract.STATUS_MESSAGE,
            {
                "v": contract.STATUS_VERSION,
                "generation": report.get("generation"),
                "path": self.status_path,
            },
        )


def _outcome(state: str, reason: str, hash: str) -> Dict[str, str]:
    return {"state": state, "reason": reason, "hash": hash, "at": _now()}


def waiting(report: Dict[str, Any]) -> bool:
    return any(o.get("state") == WAITING for o in report.get("requests", {}).values())


def entry_data(entry: Entry) -> Dict[str, Any]:
    return entry.data
