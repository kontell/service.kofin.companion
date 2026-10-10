"""The reconciler with a fake kind, against a profile and a fake Kodi."""

import json

import pytest

from companion import contract, reconciler
from companion.handlers.base import Context, Handler, Refused, Retry
from companion.ledger import Ledger
from tests.unit import kodifixtures


class FakeHandler(Handler):
    kind = "fake"
    family = "groupVersions"
    needs = ("video",)

    def __init__(self):
        self.applied = []
        self.withdrawn = []
        self.holds = True
        self.fail = None

    def apply(self, request, context):
        if self.fail is not None:
            raise self.fail
        self.applied.append(
            (request.id, request.fields, context.namespace, context.device)
        )
        return {"rows": [1, 2]}

    def withdraw(self, entry, context):
        self.withdrawn.append(entry.data)

    def confirm(self, request, entry, context):
        return self.holds


@pytest.fixture
def rig(profile, kodi):
    for name, maker in (("MyVideos149.db", kodifixtures.create_video_db),):
        maker(str(profile / "Database" / name))
    kofin = profile / "addon_data" / "plugin.video.kofin"
    (kofin / "api-capabilities.json").write_text('{"backend": "api"}', encoding="utf-8")
    own = profile / "addon_data" / "service.kofin.companion"
    handler = FakeHandler()
    ledger = Ledger(str(own / "written.json"))
    ledger.load()
    worker = reconciler.Reconciler(
        {"fake": handler},
        ledger,
        str(kofin / "companion" / "requests.json"),
        str(kofin / "api-capabilities.json"),
        str(own / "status.json"),
        setting=lambda name: kodi.settings.get(name, True),
        busy=lambda: kodi.scanning,
        version=lambda: "0.1.0",
    )

    def publish(requests, generation=1, version=2):
        (kofin / "companion" / "requests.json").write_text(
            json.dumps(
                {
                    "v": version,
                    "generation": generation,
                    "namespace": "ns",
                    "device": "dev",
                    "kinds": sorted({r["kind"] for r in requests}),
                    "requests": requests,
                }
            ),
            encoding="utf-8",
        )

    def status():
        return json.loads((own / "status.json").read_text(encoding="utf-8"))

    return type("Rig", (), {})(), handler, ledger, worker, publish, status, kodi, kofin


def test_a_request_is_applied_recorded_and_reported(rig):
    _, handler, ledger, worker, publish, status, kodi, _ = rig
    publish([{"id": "r1", "kind": "fake", "library": "L", "item": "I", "x": 1}])
    report = worker.run("ping")
    assert handler.applied == [
        ("r1", {"library": "L", "item": "I", "x": 1}, "ns", "dev")
    ]
    assert ledger.get("r1").data == {"rows": [1, 2]}
    assert report["requests"]["r1"]["state"] == "done"
    assert report["summary"] == {"done": 1, "waiting": 0, "refused": 0}
    assert status()["generation"] == 1 and status()["kinds"] == ["fake"]
    assert status()["gate"]["video"] == {"version": 149, "ok": True, "reason": ""}
    (ping,) = kodi.notified
    assert (
        ping["message"] == contract.STATUS_MESSAGE
        and ping["sender"] == "service.kofin.companion"
    )
    assert ping["data"]["generation"] == 1 and ping["data"]["path"].endswith(
        "status.json"
    )
    # The same request again is confirmed, not re-applied.
    worker.run("timer")
    assert len(handler.applied) == 1
    # A request that no longer holds is applied again.
    handler.holds = False
    worker.run("timer")
    assert len(handler.applied) == 2


def test_a_changed_request_is_applied_again_and_a_withdrawn_one_undone(rig):
    _, handler, ledger, worker, publish, status, _, _ = rig
    publish([{"id": "r1", "kind": "fake", "x": 1}])
    worker.run("ping")
    publish([{"id": "r1", "kind": "fake", "x": 2}], generation=2)
    worker.run("ping")
    assert [a[1]["x"] for a in handler.applied] == [1, 2]
    publish([], generation=3)
    report = worker.run("ping")
    assert handler.withdrawn == [{"rows": [1, 2]}]
    assert ledger.entries == {} and report["requests"] == {}
    assert status()["generation"] == 3


def test_refusals_name_their_reason(rig):
    _, handler, ledger, worker, publish, status, kodi, _ = rig
    publish(
        [
            {"id": "u1", "kind": "unknown"},
            {"id": "f1", "kind": "fake"},
        ]
    )
    kodi.settings["groupVersions"] = False
    report = worker.run("ping")
    assert report["requests"]["u1"] == dict(
        report["requests"]["u1"], state="refused", reason="unsupported kind"
    )
    assert report["requests"]["f1"]["reason"] == "disabled: groupVersions"
    kodi.settings["groupVersions"] = True
    handler.fail = Refused("not owned")
    report = worker.run("ping")
    assert report["requests"]["f1"] == dict(
        report["requests"]["f1"], state="refused", reason="not owned"
    )
    assert ledger.get("f1") is None


def test_the_gate_refuses_a_kind_whose_database_is_not_served(rig, profile):
    _, handler, _, worker, publish, _, _, _ = rig
    from companion import schema

    (profile / "Database" / "MyVideos149.db").unlink()
    (profile / "Database" / "MyVideos148.db").write_bytes(b"")
    schema.reset_cache()
    publish([{"id": "f1", "kind": "fake"}])
    report = worker.run("ping")
    assert report["requests"]["f1"]["state"] == "refused"
    assert report["requests"]["f1"]["reason"] == "gate: MyVideos v148 is not served"
    assert handler.applied == []


def test_a_scan_defers_writes_but_not_confirmations(rig):
    _, handler, ledger, worker, publish, _, kodi, _ = rig
    publish([{"id": "f1", "kind": "fake"}])
    kodi.scanning = True
    report = worker.run("ping")
    assert report["requests"]["f1"] == dict(
        report["requests"]["f1"], state="waiting", reason="scan"
    )
    assert reconciler.waiting(report) and handler.applied == []
    kodi.scanning = False
    worker.run("timer")
    assert len(handler.applied) == 1
    kodi.scanning = True
    report = worker.run("timer")
    assert report["requests"]["f1"]["state"] == "done"
    publish([], generation=2)
    report = worker.run("ping")
    assert report["requests"]["f1"]["reason"] == "withdrawal waits for the scan"
    assert handler.withdrawn == [] and ledger.get("f1") is not None
    kodi.scanning = False
    worker.run("timer")
    assert handler.withdrawn == [{"rows": [1, 2]}]


def test_a_retry_and_a_failure_are_waiting_not_refused(rig):
    _, handler, ledger, worker, publish, _, _, _ = rig
    publish([{"id": "f1", "kind": "fake"}])
    handler.fail = Retry("locked")
    report = worker.run("ping")
    assert report["requests"]["f1"]["reason"] == "locked"
    handler.fail = RuntimeError("boom")
    report = worker.run("ping")
    assert report["requests"]["f1"]["state"] == "waiting"
    assert ledger.get("f1") is None
    handler.fail = None
    report = worker.run("ping")
    assert report["requests"]["f1"]["state"] == "done"


def test_idle_beside_main_or_without_kofin_and_on_an_unreadable_document(rig, profile):
    _, handler, _, worker, publish, status, kodi, kofin = rig
    (kofin / "api-capabilities.json").write_text('{"backend": "sql"}', encoding="utf-8")
    report = worker.run("start")
    assert report["idle"] == "kofin's sql build writes its own rows"
    assert status()["idle"] == report["idle"] and len(kodi.notified) == 1
    worker.run("timer")
    assert len(kodi.notified) == 1  # the same idle reason is not re-published
    (kofin / "api-capabilities.json").unlink()
    report = worker.run("timer")
    assert report["idle"] == "kofin's API build is not installed"
    (kofin / "api-capabilities.json").write_text('{"backend": "api"}', encoding="utf-8")
    (kofin / "companion" / "requests.json").write_text("{broken", encoding="utf-8")
    report = worker.run("timer")
    assert report["idle"].startswith("unreadable document")
    publish([{"id": "f1", "kind": "fake"}], version=9)
    report = worker.run("timer")
    assert report["idle"] == "document version 9 is not served"
    assert handler.applied == []


def test_the_empty_document_withdraws_everything(rig):
    _, handler, ledger, worker, publish, _, _, kofin = rig
    publish([{"id": "f1", "kind": "fake"}])
    worker.run("ping")
    (kofin / "companion" / "requests.json").unlink()
    worker.run("timer")
    assert handler.withdrawn == [{"rows": [1, 2]}] and ledger.entries == {}


def test_a_ledger_entry_of_an_unknown_kind_is_kept(rig):
    _, _, ledger, worker, publish, _, _, _ = rig
    ledger.put("old", "retired", "h", {"rows": [9]})
    publish([])
    worker.run("ping")
    assert ledger.get("old") is not None


def test_context_reads_the_gated_version():
    context = Context(
        "ns", "dev", {"video": {"version": 149, "ok": True, "reason": ""}}
    )
    assert context.version("video") == 149 and context.version("texture") == 0


def test_a_start_sweeps_each_handlers_leftovers_once(rig):
    _, handler, ledger, worker, publish, status, kodi, _ = rig
    swept = []
    handler.sweep = (
        lambda context, entries: swept.append([e.kind for e in entries]) or 2
    )
    ledger.put("old", "fake", "h", {"rows": [1]})
    publish([{"id": "old", "kind": "fake"}])
    report = worker.run("start")
    assert report["swept"] == {"fake": 2} and swept == [["fake"]]
    report = worker.run("timer")
    assert "swept" not in report and len(swept) == 1
    kodi.scanning = True
    report = worker.run("start")
    assert "swept" not in report
