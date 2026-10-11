"""The ledger file: missing is empty, anything else unreadable is an error."""

import json

import pytest

from companion import contract
from companion.ledger import Ledger, LedgerError


def test_no_file_is_an_empty_ledger_and_a_saved_one_reads_back(tmp_path):
    path = tmp_path / "written.json"
    ledger = Ledger(str(path))
    assert not ledger.loaded
    ledger.load()
    assert ledger.loaded and ledger.entries == {}
    ledger.put("r1", "fake", "h1", {"rows": [1]})
    ledger.save()
    again = Ledger(str(path))
    again.load()
    assert again.get("r1").data == {"rows": [1]} and again.get("r1").hash == "h1"


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        json.dumps({"v": contract.LEDGER_VERSION + 1, "requests": {}}),
        json.dumps({"v": contract.LEDGER_VERSION, "requests": []}),
        json.dumps({"v": contract.LEDGER_VERSION, "requests": {"r1": "x"}}),
    ],
)
def test_a_file_that_is_not_a_ledger_raises_and_is_never_saved_over(tmp_path, content):
    """Read as empty, the next apply would replace the only record of the
    rows a withdrawal has to undo."""
    path = tmp_path / "written.json"
    path.write_text(content, encoding="utf-8")
    ledger = Ledger(str(path))
    with pytest.raises(LedgerError):
        ledger.load()
    assert not ledger.loaded
    ledger.put("r2", "fake", "h2", {})
    ledger.save()
    assert path.read_text(encoding="utf-8") == content
