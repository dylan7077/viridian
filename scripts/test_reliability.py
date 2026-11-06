"""Regression checks for image limits and live index replacement."""
import io
import json
from pathlib import Path

from PIL import Image

from src import engine
import config


def test_rejects_pixel_bomb_before_opencv(monkeypatch):
    class Header:
        width, height = 10000, 10000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(engine.Image, "open", lambda *args: Header())
    monkeypatch.setattr(engine.cv2, "imdecode", lambda *args: (_ for _ in ()).throw(
        AssertionError("OpenCV should not decode oversized images")))
    assert engine.decode_image(b"header") is None


def test_normal_image_decodes():
    buf = io.BytesIO()
    Image.new("RGB", (100, 140), "red").save(buf, format="PNG")
    assert engine.decode_image(buf.getvalue()).shape == (140, 100, 3)


def test_invalid_manual_corners_are_not_sent_to_opencv():
    import numpy as np
    img = np.zeros((140, 100, 3), dtype=np.uint8)
    for points in (123, [[0, 0]] * 4, [[0, 0], [1, 0], [1, float("nan")], [0, 1]],
                   [[-1, 0], [1, 0], [1, 1], [0, 1]]):
        assert engine._px_corners(points, img) is None
    assert engine._px_corners([[0, 0], [1, 0], [1, 1], [0, 1]], img) is not None


def test_engine_reloads_changed_index(tmp_path, monkeypatch):
    index_path = tmp_path / "index.json"
    index_path.write_text("[]")
    monkeypatch.setattr(config, "INDEX_PATH", index_path)
    monkeypatch.setattr(engine, "_index", None)
    monkeypatch.setattr(engine, "_index_stamp", None)
    # CardIndex has an old default path bound at import time; construct with the active path.
    original = engine.CardIndex
    monkeypatch.setattr(engine, "CardIndex", lambda: original(index_path))
    assert len(engine.get_index()) == 0
    index_path.write_text(json.dumps([{"id": "test", "phash": "0000000000000000"}]))
    assert len(engine.get_index()) == 1


def test_stale_orb_database_does_not_cover_new_cards():
    from types import SimpleNamespace
    index = SimpleNamespace(cards=[{"id": "old"}, {"id": "new"}])
    old = SimpleNamespace(ok=True, _id_to_idx={"old": 0})
    current = SimpleNamespace(ok=True, _id_to_idx={"old": 0, "new": 1})
    assert not engine._orb_covers_index(index, old)
    assert engine._orb_covers_index(index, current)
