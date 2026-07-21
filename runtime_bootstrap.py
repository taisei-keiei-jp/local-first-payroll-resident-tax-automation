from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_runtime(app_root: Path | str | None = None) -> Path | None:
    """Place the project-local vendor directory first on sys.path.

    The default root is resolved from this module, so the application keeps
    working when its containing folder is renamed or moved.
    """

    root = (
        Path(app_root).expanduser().resolve()
        if app_root is not None
        else Path(__file__).resolve().parent
    )
    vendor_dir = (root / "vendor").resolve()
    if not vendor_dir.is_dir():
        return None

    vendor_path = str(vendor_dir)
    existing_indexes = [
        index
        for index, entry in enumerate(sys.path)
        if entry and Path(entry).expanduser().resolve() == vendor_dir
    ]
    for index in reversed(existing_indexes):
        sys.path.pop(index)
    sys.path.insert(0, vendor_path)
    return vendor_dir
