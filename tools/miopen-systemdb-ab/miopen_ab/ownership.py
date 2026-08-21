"""Restore bind-mounted output ownership to the invoking host user."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def restore_host_ownership(path: Path) -> bool:
    """Recursively chown *path* to HOST_UID:HOST_GID when running as root in Docker.

    Matches the pattern used by data/miopen/tune.sh so experiment artifacts written
    inside the container are owned by the user who invoked run_experiment.sh on the host.
    """
    host_uid = os.environ.get("HOST_UID")
    host_gid = os.environ.get("HOST_GID")
    if host_uid is None or host_gid is None:
        logger.debug("HOST_UID/HOST_GID not set; skipping ownership restore")
        return False

    if not path.exists():
        logger.debug("Path %s does not exist; skipping ownership restore", path)
        return False

    logger.info("Restoring ownership of %s to %s:%s", path, host_uid, host_gid)
    proc = subprocess.run(
        ["chown", "-hR", f"{host_uid}:{host_gid}", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "chown failed for %s (exit %s): %s",
            path,
            proc.returncode,
            (proc.stderr or proc.stdout).strip(),
        )
        return False
    return True
