import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miopen_ab.ownership import restore_host_ownership


def test_restore_host_ownership_skips_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HOST_UID", raising=False)
    monkeypatch.delenv("HOST_GID", raising=False)
    assert restore_host_ownership(tmp_path) is False


def test_restore_host_ownership_runs_chown(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_UID", "1000")
    monkeypatch.setenv("HOST_GID", "1000")
    target = tmp_path / "run"
    target.mkdir()

    with patch("miopen_ab.ownership.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        assert restore_host_ownership(target) is True
        mock_run.assert_called_once_with(
            ["chown", "-hR", "1000:1000", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
