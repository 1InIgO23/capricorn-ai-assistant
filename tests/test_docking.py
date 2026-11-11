"""Tests for the docking geometry (screen edges and other app windows)."""
from capri.character.docking import (AppWindow, DockTarget, Rect, dock_position, find_dock,
                                     window_edges)

SCREEN = Rect(0, 0, 1920, 1040)
S = 200


def char_at(x: int, y: int) -> Rect:
    return Rect(x, y, S, S)


def test_screen_edges_take_priority() -> None:
    window = AppWindow(1, "Editor", Rect(10, 100, 800, 600))
    target = find_dock(char_at(5, 300), SCREEN, [window])
    assert target.kind == "screen" and target.edge == "left"
    assert dock_position(target, S, hidden=True) == (-100, 300)
    assert dock_position(target, S, hidden=False) == (4, 300)


def test_free_drop_does_not_dock() -> None:
    window = AppWindow(1, "Editor", Rect(1000, 100, 600, 600))
    assert find_dock(char_at(400, 400), SCREEN, [window]) is None


def test_dock_to_window_left_and_right_edges() -> None:
    window = AppWindow(7, "Editor", Rect(800, 100, 700, 700))
    left = find_dock(char_at(800 - S - 10, 300), SCREEN, [window])
    assert (left.kind, left.edge, left.handle) == ("window", "left", 7)
    assert dock_position(left, S, hidden=True) == (700, 300)    # half behind the window
    assert dock_position(left, S, hidden=False) == (596, 300)   # fully beside it
    right = find_dock(char_at(1500 + 10, 300), SCREEN, [window])
    assert right.edge == "right"
    assert dock_position(right, S, hidden=True) == (1400, 300)


def test_dock_to_window_top_edge_and_overlap() -> None:
    window = AppWindow(3, "Browser", Rect(400, 500, 900, 400))
    target = find_dock(char_at(600, 500 - S + 40), SCREEN, [window])  # overlapping a bit
    assert target.edge == "top"
    assert dock_position(target, S, hidden=True) == (600, 400)


def test_covered_edge_is_ignored() -> None:
    below = AppWindow(1, "Back", Rect(800, 100, 700, 700))
    cover = AppWindow(2, "Front", Rect(750, 0, 200, 1000))  # covers the back window's left edge
    # next to the hidden edge she docks to the front window instead, never to the back one
    target = find_dock(char_at(800 - S - 10, 300), SCREEN, [cover, below])
    assert target is not None and target.handle == 2
    assert all(t.handle != 1 or t.edge != "left" for t in window_edges([cover, below], SCREEN, S))


def test_tiny_windows_and_screen_bordering_edges_are_skipped() -> None:
    tiny = AppWindow(1, "Tiny", Rect(800, 300, 100, 80))
    assert find_dock(char_at(800 - S - 5, 250), SCREEN, [tiny]) is None
    maximized = AppWindow(2, "Max", Rect(0, 0, 1920, 1040))
    assert window_edges([maximized], SCREEN, S) == []


def test_window_edges_for_spontaneous_peeks() -> None:
    window = AppWindow(5, "Notes", Rect(600, 300, 600, 500))
    edges = {t.edge for t in window_edges([window], SCREEN, S)}
    assert edges == {"left", "right", "top"}


def test_following_a_moved_window() -> None:
    target = DockTarget("window", "right", Rect(100, 100, 500, 500), along=50, handle=1)
    moved = target.with_rect(Rect(300, 200, 500, 500))
    assert dock_position(moved, S, hidden=True) == (700, 250)
