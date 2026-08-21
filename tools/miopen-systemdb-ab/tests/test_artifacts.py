import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.artifacts import collect_artifacts, write_artifacts_manifest


def test_artifacts_manifest_lists_user_db_files(tmp_path):
    output_dir = tmp_path / "run"
    arm_a_db = output_dir / "arm_a" / "user_db"
    arm_b_merged = output_dir / "arm_b" / "tuning_merged"
    arm_b_device = output_dir / "arm_b" / "tuning" / "device_0"
    arm_a_db.mkdir(parents=True)
    arm_b_merged.mkdir(parents=True)
    arm_b_device.mkdir(parents=True)

    (arm_a_db / "gfx942130.HIP.3_5_2.udb.txt").write_text("key=solver\n")
    (arm_b_merged / "gfx942130.HIP.3_5_2.udb.txt").write_text("key=solver2\n")
    (arm_b_device / "gfx942130.HIP.3_5_2.udb.txt").write_text("key=solver2\n")

    manifest_path = write_artifacts_manifest(output_dir)
    payload = json.loads(manifest_path.read_text())

    assert payload["arm_a"]["udb_files"]
    assert payload["arm_b"]["merged_udb_files"]
    assert payload["arm_b"]["per_device_databases"][0]["udb_files"]
