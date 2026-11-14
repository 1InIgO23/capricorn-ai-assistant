"""Tests for the file tools' safety checks."""
import os
import sys
import threading
from pathlib import Path

import pytest

from capri.tools.base import ToolContext
from capri.tools import files, scripts
from capri.tools.safety import SafetyError, SafetyPolicy, default_policy


@pytest.fixture()
def sandbox(tmp_path: Path):
    system = tmp_path / "Windows"
    readonly = tmp_path / "program"
    home = tmp_path / "home"
    work = home / "workspace"
    for folder in (system / "System32", readonly, work):
        folder.mkdir(parents=True)
    (system / "System32" / "kernel.dll").write_text("x")
    (readonly / "app.py").write_text("x")
    policy = SafetyPolicy(forbidden_roots=[str(system)], readonly_roots=[str(readonly)],
                          protected_folders=[str(home)])
    asked: list = []

    def confirm(request) -> bool:
        asked.append(request)
        return confirm.answer

    confirm.answer = True
    ctx = ToolContext(workspace=work, policy=policy, confirm=confirm,
                      cancel=threading.Event())
    return ctx, asked, confirm, tmp_path


def test_system_folder_is_fully_off_limits(sandbox) -> None:
    ctx, _asked, _confirm, root = sandbox
    target = root / "Windows" / "System32" / "kernel.dll"
    with pytest.raises(SafetyError):
        ctx.policy.check_read(target)
    with pytest.raises(SafetyError):
        ctx.policy.check_modify(root / "Windows")
    with pytest.raises(SafetyError):
        files.read_file({"path": str(target)}, ctx)
    with pytest.raises(SafetyError):
        files.list_directory({"path": str(root / "Windows")}, ctx)
    with pytest.raises(SafetyError):
        files.delete_path({"paths": [str(target)]}, ctx)


def test_traversal_and_case_tricks_do_not_escape(sandbox) -> None:
    ctx, _asked, _confirm, root = sandbox
    sneaky = root / "home" / ".." / "Windows" / "System32" / "kernel.dll"
    assert ctx.policy.is_forbidden(sneaky)
    with pytest.raises(SafetyError):
        files.write_file({"path": str(sneaky), "content": "x", "overwrite": True}, ctx)
    # a sibling whose name merely starts with the protected name is fine
    assert not ctx.policy.is_forbidden(root / "WindowsOld" / "file.txt")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_into_system_folder_is_caught(sandbox) -> None:
    ctx, _asked, _confirm, root = sandbox
    link = root / "home" / "workspace" / "shortcut"
    os.symlink(root / "Windows", link)
    assert ctx.policy.is_forbidden(link / "System32" / "kernel.dll")


def test_readonly_and_protected_folders(sandbox) -> None:
    ctx, _asked, _confirm, root = sandbox
    ctx.policy.check_read(root / "program" / "app.py")
    with pytest.raises(SafetyError):
        ctx.policy.check_modify(root / "program" / "app.py")
    with pytest.raises(SafetyError):
        ctx.policy.check_modify(root / "home")          # the folder itself
    ctx.policy.check_modify(root / "home" / "notes.txt")  # but its contents are fine
    with pytest.raises(SafetyError):
        ctx.policy.check_modify(Path(root.anchor))       # drive root


def test_modifying_actions_ask_first_and_respect_denial(sandbox) -> None:
    ctx, asked, confirm, _root = sandbox
    confirm.answer = False
    result = files.write_file({"path": "note.txt", "content": "hello"}, ctx)
    assert not result.ok and len(asked) == 1
    assert not (ctx.workspace / "note.txt").exists()
    assert "note.txt" in asked[0].details[0]

    confirm.answer = True
    assert files.write_file({"path": "note.txt", "content": "hello"}, ctx).ok
    assert (ctx.workspace / "note.txt").read_text() == "hello"
    with pytest.raises(ValueError):  # no silent overwrite
        files.write_file({"path": "note.txt", "content": "again"}, ctx)

    assert files.rename_path({"path": "note.txt", "new_name": "renamed.txt"}, ctx).ok
    (ctx.workspace / "sub").mkdir()
    assert files.move_path({"sources": ["renamed.txt"], "destination": "sub"}, ctx).ok
    assert (ctx.workspace / "sub" / "renamed.txt").exists()
    assert len(asked) == 4  # every modifying call produced exactly one confirmation


def test_rename_rejects_paths_in_new_name(sandbox) -> None:
    ctx, _asked, _confirm, _root = sandbox
    (ctx.workspace / "a.txt").write_text("x")
    with pytest.raises(ValueError):
        files.rename_path({"path": "a.txt", "new_name": "..\\..\\evil.txt"}, ctx)


def test_delete_goes_to_recycle_bin_only_after_confirmation(sandbox, monkeypatch) -> None:
    ctx, asked, confirm, _root = sandbox
    victim = ctx.workspace / "old.txt"
    victim.write_text("x")
    trashed: list[str] = []
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda path: trashed.append(path))
    confirm.answer = False
    assert not files.delete_path({"paths": ["old.txt"]}, ctx).ok
    assert trashed == []
    confirm.answer = True
    assert files.delete_path({"paths": ["old.txt"]}, ctx).ok
    assert trashed == [str(victim)]
    # "Recycle: ..." on Windows, "Trash: ..." on macOS
    assert asked[-1].details[0].split(":")[0] in ("Recycle", "Trash")


def test_scripts_stay_in_workspace_and_need_confirmation(sandbox) -> None:
    ctx, asked, confirm, _root = sandbox
    with pytest.raises(ValueError):
        scripts.script_path("notes.txt", ctx)
    assert scripts.script_path("..\\..\\evil.py", ctx).parent == ctx.workspace
    assert scripts.script_path("C:/Windows/evil.py", ctx).parent == ctx.workspace

    assert scripts.save_script({"filename": "hello.py", "content": "print('hi')"}, ctx).ok
    confirm.answer = False
    result = scripts.run_script({"filename": "hello.py"}, ctx)
    assert not result.ok and asked[-1].code == "print('hi')" and asked[-1].kind == "script"

    confirm.answer = True
    result = scripts.run_script({"filename": "hello.py"}, ctx)
    assert result.ok and "hi" in result.content

    scripts.save_script({"filename": "boom.py", "content": "raise SystemExit(3)"}, ctx)
    assert not scripts.run_script({"filename": "boom.py"}, ctx).ok


def test_script_timeout_kills_the_process(sandbox) -> None:
    ctx, _asked, _confirm, _root = sandbox
    ctx.script_timeout = 1
    (ctx.workspace / "slow.py").write_text("import time\ntime.sleep(30)\n")
    result = scripts.run_script({"filename": "slow.py"}, ctx)
    assert not result.ok and "timed out" in result.summary


def test_default_policy_protects_app_data_and_home() -> None:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    policy = default_policy(extra_forbidden=[home / "secret_capri_cfg"])
    assert policy.is_forbidden(home / "secret_capri_cfg" / "settings.json")
    with pytest.raises(SafetyError):
        policy.check_modify(home)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system folders")
def test_default_policy_blocks_windows_system_folders() -> None:
    policy = default_policy()
    for path in (r"C:\Windows\System32\drivers\etc\hosts", r"C:\Program Files\App\x.exe",
                 r"c:\windows", r"C:\PROGRAM FILES (X86)\thing", r"C:\ProgramData\x",
                 r"\\?\C:\Windows\notepad.exe", r"C:\Users\..\Windows\win.ini"):
        assert policy.is_forbidden(path), path
    with pytest.raises(SafetyError):
        policy.check_modify("C:\\")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system folders")
def test_default_policy_blocks_macos_system_folders() -> None:
    policy = default_policy()
    for path in ("/System/Library/CoreServices", "/system/library/x",  # case-insensitive disk
                 "/Library/Preferences/x.plist", "/Applications/Safari.app",
                 "/usr/bin/python3", "/etc/hosts", "/private/etc/hosts", "/bin/zsh",
                 "/Users/../System/x"):
        assert policy.is_forbidden(path), path
    home = Path.home()
    for folder in (home, home / "Library", home / "Movies", home / "Desktop"):
        with pytest.raises(SafetyError):
            policy.check_modify(folder)
    assert not policy.is_forbidden(home / "Library" / "notes.txt")
    with pytest.raises(SafetyError):
        policy.check_modify("/")
