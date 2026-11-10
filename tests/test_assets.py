"""Tests for the character asset detection logic."""
import json
from pathlib import Path

from capri.character.assets import DEFAULT_FPS, FALLBACKS, STATES, scan_assets


def touch(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"\x89PNG\r\n")


def test_empty_or_missing_folder_uses_placeholders(tmp_path: Path) -> None:
    index = scan_assets(tmp_path / "does_not_exist")
    assert index.found == {}
    assert index.placeholder_states() == list(STATES)
    assert scan_assets(tmp_path).resolve("idle") is None


def test_single_image_per_state(tmp_path: Path) -> None:
    touch(tmp_path, "idle.png", "happy.png")
    index = scan_assets(tmp_path)
    assert index.has_own("idle") and index.has_own("happy")
    assert index.found["idle"].animated is False
    assert [p.name for p in index.found["idle"].frames] == ["idle.png"]


def test_frames_take_priority_and_play_in_numeric_order(tmp_path: Path) -> None:
    touch(tmp_path, "idle.png", "idle_10.png", "idle_2.png", "idle_1.png")
    assets = scan_assets(tmp_path).found["idle"]
    assert assets.animated is True
    assert [p.name for p in assets.frames] == ["idle_1.png", "idle_2.png", "idle_10.png"]


def test_fallback_chain(tmp_path: Path) -> None:
    touch(tmp_path, "idle.png")
    index = scan_assets(tmp_path)
    assert index.resolve("searching").state == "idle"
    assert index.resolve("peek").state == "idle"
    assert index.resolve("error").state == "idle"
    touch(tmp_path, "thinking.png", "alert.png")
    index = scan_assets(tmp_path)
    assert index.resolve("searching").state == "thinking"
    assert index.resolve("coding").state == "thinking"
    assert index.resolve("error").state == "alert"
    assert index.resolve("happy").state == "idle"


def test_standby_never_borrows_another_image(tmp_path: Path) -> None:
    touch(tmp_path, "idle.png", "sleeping.png")
    index = scan_assets(tmp_path)
    assert index.resolve("standby") is None
    assert index.placeholder_states() == ["standby"]


def test_unknown_and_malformed_files_are_ignored(tmp_path: Path) -> None:
    touch(tmp_path, "dancing.png", "idle_0.png", "idle-1.png", "IDLE.PNG")
    (tmp_path / "notes.txt").write_text("hi")
    index = scan_assets(tmp_path)
    assert index.has_own("idle")  # case-insensitive match of IDLE.PNG
    assert set(index.ignored) == {"dancing.png", "idle_0.png", "idle-1.png"}


def test_animation_json(tmp_path: Path) -> None:
    touch(tmp_path, "idle_1.png", "idle_2.png")
    (tmp_path / "animation.json").write_text(json.dumps(
        {"default_fps": 12, "states": {"idle": {"fps": 4}, "bogus": {"fps": 9},
                                        "blink": {"fps": -1}}}))
    index = scan_assets(tmp_path)
    assert index.fps_for("idle") == 4
    assert index.fps_for("talking") == 12
    assert "bogus" not in index.fps and "blink" not in index.fps


def test_broken_animation_json_falls_back(tmp_path: Path) -> None:
    (tmp_path / "animation.json").write_text("{not json")
    assert scan_assets(tmp_path).fps_for("idle") == DEFAULT_FPS


def test_every_state_has_a_fallback_entry() -> None:
    assert set(FALLBACKS) == set(STATES)
    for chain in FALLBACKS.values():
        assert all(state in STATES for state in chain)
