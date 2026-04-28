"""Save a geocontext snapshot directly to a local folder. No git involved."""

import shutil
from pathlib import Path


def save_to_folder(target_dir, base_path, files):
    """Copy `files` into <target_dir>/<base_path>.

    Mirrors the GitHub flow's pruning behavior: <base>/datasets/ is wiped
    first so layers removed from the QGIS project also disappear on disk.
    Files outside datasets/ (like gcx.json itself) are simply overwritten.

    Returns the list of paths written, relative to target_dir.
    """
    target_dir = Path(target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    base = target_dir / base_path if base_path else target_dir

    datasets_dir = base / "datasets"
    if datasets_dir.exists():
        shutil.rmtree(datasets_dir)

    written = []
    for rel_path, src in files:
        dst = base / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        written.append(str(dst.relative_to(target_dir)))
    return written
