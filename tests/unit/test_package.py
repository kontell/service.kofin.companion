import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_zip_carries_the_addon_and_nothing_of_the_repository(tmp_path):
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build.py"), str(tmp_path)],
        check=True,
        capture_output=True,
    )
    (archive,) = tmp_path.glob("service.kofin.companion-*.zip")
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
    assert "service.kofin.companion/addon.xml" in names
    assert "service.kofin.companion/service.py" in names
    assert "service.kofin.companion/lib/companion/reconciler.py" in names
    assert "service.kofin.companion/resources/settings.xml" in names
    assert not any("/tests/" in n or "/tools/" in n or "/.github/" in n for n in names)
    assert not any(n.endswith(("README.md", "mypy.ini", ".gitignore")) for n in names)


def test_staging_matches_the_zip(tmp_path):
    staged = tmp_path / "stage"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build.py"), "--stage", str(staged)],
        check=True,
        capture_output=True,
    )
    assert (staged / "addon.xml").exists() and (
        staged / "lib" / "companion" / "schema.py"
    ).exists()
    assert not (staged / "tests").exists()
