import json

import pytest

from companion import contract
from companion.ledger import Ledger


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_missing_document_is_the_empty_document(tmp_path):
    assert contract.read_document(str(tmp_path / "requests.json")) is contract.EMPTY


def test_version_one_and_two_documents_parse_alike(tmp_path):
    path = tmp_path / "requests.json"
    request = {
        "id": "r1",
        "kind": "group_versions",
        "library": "lib",
        "item": "it",
        "owner": {"movieid": 1},
    }
    _write(path, {"v": 1, "generation": 3, "namespace": "ns", "requests": [request]})
    one = contract.read_document(str(path))
    assert (one.version, one.generation, one.namespace, one.device, one.kinds) == (
        1,
        3,
        "ns",
        "",
        (),
    )
    assert list(one.requests) == ["r1"]
    parsed = one.requests["r1"]
    assert (parsed.kind, parsed.library, parsed.item) == ("group_versions", "lib", "it")
    assert parsed.fields == {"library": "lib", "item": "it", "owner": {"movieid": 1}}
    _write(
        path,
        {
            "v": 2,
            "generation": 4,
            "namespace": "ns",
            "device": "dev",
            "kinds": ["group_versions"],
            "requests": [request],
        },
    )
    two = contract.read_document(str(path))
    assert (two.version, two.device, two.kinds) == (2, "dev", ("group_versions",))
    # The hash is the request's content, not its id or the document around it.
    assert two.requests["r1"].hash == parsed.hash
    assert contract.content_hash(dict(request, id="other")) == parsed.hash
    assert contract.content_hash(dict(request, owner={"movieid": 2})) != parsed.hash


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"generation": 1, "requests": []}',
        '{"v": 2, "requests": {}}',
        '{"v": 2, "requests": [{"kind": "x"}]}',
        '{"v": 2, "requests": [{"id": "a"}]}',
        '{"v": 2, "requests": [{"id": "a", "kind": "x"}, {"id": "a", "kind": "x"}]}',
    ],
)
def test_a_document_that_cannot_be_read_raises(tmp_path, raw):
    path = tmp_path / "requests.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(contract.DocumentError):
        contract.read_document(str(path))


def test_kofin_backend_reads_the_capability_report(tmp_path):
    path = tmp_path / "api-capabilities.json"
    assert contract.kofin_backend(str(path)) is None
    path.write_text('{"backend": "api"}', encoding="utf-8")
    assert contract.kofin_backend(str(path)) == "api"
    path.write_text("[]", encoding="utf-8")
    assert contract.kofin_backend(str(path)) is None


def test_the_ping_payload_may_be_bare_or_list_wrapped():
    assert contract.decode_ping('{"v": 1, "generation": 2}') == {
        "v": 1,
        "generation": 2,
    }
    assert contract.decode_ping('[{"v": 1}]') == {"v": 1}
    assert contract.decode_ping("nope") is None
    assert contract.decode_ping("[1]") is None


def test_the_ledger_round_trips_and_writes_only_when_changed(tmp_path):
    path = tmp_path / "own" / "written.json"
    ledger = Ledger(str(path))
    ledger.load()
    assert ledger.entries == {}
    ledger.save()
    assert not path.exists()
    ledger.put("r1", "group_versions", "abcd", {"movieid": 5})
    ledger.save()
    again = Ledger(str(path))
    again.load()
    assert again.get("r1") is not None
    assert (
        again.get("r1").data == {"movieid": 5}
        and again.get("r1").kind == "group_versions"
    )
    stamp = path.stat().st_mtime_ns
    again.save()
    assert path.stat().st_mtime_ns == stamp
    again.remove("r1")
    again.save()
    once_more = Ledger(str(path))
    once_more.load()
    assert once_more.entries == {}


def test_write_json_replaces_whole_files(tmp_path):
    path = tmp_path / "a" / "b.json"
    contract.write_json(str(path), {"x": 1})
    contract.write_json(str(path), {"x": 2})
    assert contract.read_json(str(path)) == {"x": 2}
    assert not (tmp_path / "a" / "b.json.part").exists()
