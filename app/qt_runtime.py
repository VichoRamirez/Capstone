"""Runtime helpers for Qt startup compatibility."""

from __future__ import annotations

from pathlib import Path


def prepare_qt_platform_plugins() -> None:
    """Ensure Qt can discover platform plugins on macOS.

    Some Python/Qt combinations on macOS may only scan `*.so` plugin names
    even when wheels ship `*.dylib`. We create safe symlink aliases so Qt
    can always find the `cocoa` platform plugin.
    """
    try:
        from PyQt6.QtCore import QLibraryInfo
    except Exception:
        return

    plugins_root = Path(
        QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    )
    platforms_dir = plugins_root / "platforms"
    if not platforms_dir.exists():
        return

    for dylib in platforms_dir.glob("libq*.dylib"):
        so_alias = dylib.with_suffix(".so")
        try:
            # Always refresh the alias so Qt's plugin cache sees an updated mtime.
            if so_alias.exists() or so_alias.is_symlink():
                so_alias.unlink()
            so_alias.symlink_to(dylib.name)
        except Exception:
            # Best-effort: if filesystem or policy blocks symlinks,
            # we keep running with the default Qt lookup behavior.
            continue
