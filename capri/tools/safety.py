"""Path safety checks for the file tools (pure Python, unit tested).

Three levels of protection:

* **forbidden roots** - no access at all, not even reading (system folders
  and Capri's own settings folder, which holds the API keys);
* **read-only roots** - may be read but nothing inside may be changed
  (Capri's own program folder);
* **protected folders** - the folder itself may not be moved, renamed or
  deleted, but its contents may be changed (drive roots, the user profile,
  Desktop, Documents...).

System folders differ per platform: ``C:\\Windows``, ``Program Files``... on
Windows; ``/System``, ``/Library``, ``/Applications``, ``/usr``... on macOS.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


class SafetyError(Exception):
    """Raised when an operation is not allowed on a path."""


def normalize(path: str | os.PathLike[str]) -> str:
    """Absolute, symlink-free, case-normalized form used for comparisons."""
    text = os.fspath(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    result = os.path.normcase(os.path.realpath(os.path.abspath(text)))
    if sys.platform == "darwin":
        # macOS volumes are case-insensitive by default, but normcase() does
        # nothing on POSIX: without this, "/system/library" would slip past a
        # rule for "/System". Lower-casing can only make the checks stricter.
        result = result.lower()
    return result


def is_inside(child: str, root: str) -> bool:
    """True if normalized *child* equals *root* or lies underneath it."""
    if child == root:
        return True
    prefix = root.rstrip("\\/") + os.sep
    return child.startswith(prefix)


@dataclass
class SafetyPolicy:
    """Decides which paths the tools may read or modify."""

    forbidden_roots: list[str] = field(default_factory=list)
    readonly_roots: list[str] = field(default_factory=list)
    protected_folders: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.forbidden_roots = [normalize(p) for p in self.forbidden_roots if p]
        self.readonly_roots = [normalize(p) for p in self.readonly_roots if p]
        self.protected_folders = [normalize(p) for p in self.protected_folders if p]

    def is_forbidden(self, path: str | os.PathLike[str]) -> bool:
        """True if *path* is inside a system (no access) folder."""
        norm = normalize(path)
        return any(is_inside(norm, root) for root in self.forbidden_roots)

    def check_read(self, path: str | os.PathLike[str]) -> None:
        """Raise :class:`SafetyError` unless *path* may be read or listed."""
        if self.is_forbidden(path):
            raise SafetyError(f"'{path}' is inside a protected system folder; I don't touch those.")

    def check_modify(self, path: str | os.PathLike[str]) -> None:
        """Raise :class:`SafetyError` unless *path* may be created/changed/removed."""
        self.check_read(path)
        norm = normalize(path)
        if any(is_inside(norm, root) for root in self.readonly_roots):
            raise SafetyError(f"'{path}' is part of my own program folder; it is read-only for me.")
        if norm in self.protected_folders:
            raise SafetyError(f"'{path}' is a protected folder; I can work inside it but not "
                              "move, rename or delete the folder itself.")
        anchor = os.path.normcase(os.path.splitdrive(norm)[0] + os.sep)
        if norm.rstrip("\\/") == anchor.rstrip("\\/") or norm == os.path.normcase(
                Path(norm).anchor):
            raise SafetyError(f"'{path}' is a drive root; I won't modify it.")


def _windows_system_roots() -> list[str]:
    env = os.environ
    system_drive = env.get("SystemDrive", "C:")
    roots = [
        env.get("SystemRoot") or env.get("windir") or f"{system_drive}\\Windows",
        env.get("ProgramFiles") or f"{system_drive}\\Program Files",
        env.get("ProgramFiles(x86)") or f"{system_drive}\\Program Files (x86)",
        env.get("ProgramW6432") or f"{system_drive}\\Program Files",
        env.get("ProgramData") or f"{system_drive}\\ProgramData",
        f"{system_drive}\\Windows",
        f"{system_drive}\\Program Files",
        f"{system_drive}\\Program Files (x86)",
        f"{system_drive}\\ProgramData",
        f"{system_drive}\\$Recycle.Bin",
        f"{system_drive}\\System Volume Information",
        f"{system_drive}\\Recovery",
        f"{system_drive}\\Boot",
        f"{system_drive}\\EFI",
    ]
    return roots


def _posix_system_roots() -> list[str]:
    return ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys",
            "/usr", "/var", "/System", "/Library", "/Applications"]


def _mac_system_roots() -> list[str]:
    # /etc and /var are symlinks into /private on macOS; realpath() resolves
    # them, but the real locations are listed too to make the intent explicit.
    return _posix_system_roots() + ["/private/etc", "/private/var", "/cores", "/Network",
                                    "/Volumes/Recovery"]


def _system_roots() -> list[str]:
    if sys.platform == "win32":
        return _windows_system_roots()
    if sys.platform == "darwin":
        return _mac_system_roots()
    return _posix_system_roots()


def _protected_names() -> tuple[str, ...]:
    """Folders in the home folder that may be worked in but not moved or deleted."""
    common = ("Desktop", "Documents", "Downloads", "Pictures", "Music")
    if sys.platform == "win32":
        return common + ("Videos", "AppData", "OneDrive")
    if sys.platform == "darwin":
        return common + ("Movies", "Public", "Library", "Applications",
                         "Library/Mobile Documents")  # iCloud Drive
    return common + ("Videos",)


def default_policy(extra_forbidden: list[str | os.PathLike[str]] | None = None,
                   readonly: list[str | os.PathLike[str]] | None = None) -> SafetyPolicy:
    """The policy used by the app on this machine."""
    forbidden = _system_roots()
    forbidden += [os.fspath(p) for p in (extra_forbidden or [])]
    home = Path((os.environ.get("USERPROFILE") if sys.platform == "win32" else None)
                or Path.home())
    protected = [home, home.parent]
    protected += [home / name for name in _protected_names()]
    return SafetyPolicy(
        forbidden_roots=forbidden,
        readonly_roots=[os.fspath(p) for p in (readonly or [])],
        protected_folders=[os.fspath(p) for p in protected],
    )
