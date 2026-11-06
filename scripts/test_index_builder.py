"""Fresh-install and interrupted-build regressions for the TCGdex index."""
import io
import json
import sys

from PIL import Image

import config
from scripts import build_index, build_orb_db


def test_fresh_install_creates_cache_and_indexes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "INDEX_PATH", data / "index.json")
    monkeypatch.setattr(config, "IMAGE_CACHE", data / "card_images")
    monkeypatch.setattr(sys, "argv", ["build_index.py", "--sets", "test", "--limit", "1"])
    monkeypatch.setattr(build_index, "get_json", lambda url: (
        [{"id": "test", "name": "Test"}] if url.endswith("/sets") else
        {"name": "Test", "cards": [{"id": "test-1", "name": "Card", "localId": "1",
                                       "image": "https://example.test/test-1"}]}))
    img = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(img, format="PNG")

    class Response:
        status_code = 200
        content = img.getvalue()
        def raise_for_status(self):
            pass

    monkeypatch.setattr(build_index.requests, "get", lambda *args, **kwargs: Response())
    assert build_index.main() == 0
    cards = json.loads(config.INDEX_PATH.read_text())
    assert [c["id"] for c in cards] == ["test-1"]
    assert (config.IMAGE_CACHE / "test-1.jpg").is_file()


def test_file_error_keeps_previous_index(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "INDEX_PATH", tmp_path / "index.json")
    config.INDEX_PATH.write_text('[{"id":"old"}]')
    with pytest.raises(TypeError):
        build_index.save_index({"bad": {"not_json": {1}}})
    assert json.loads(config.INDEX_PATH.read_text()) == [{"id": "old"}]


def test_bad_high_resolution_image_does_not_poison_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMAGE_CACHE", tmp_path)
    img = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(img, format="PNG")

    class Response:
        status_code = 200
        def __init__(self, content):
            self.content = content
        def raise_for_status(self):
            pass

    monkeypatch.setattr(build_index.requests, "get", lambda url, **kwargs:
                        Response(img.getvalue() if url.endswith("/low.png") else b"not an image"))
    assert build_index.download_and_hash({"id": "test-1"}, "https://example.test") is None
    assert not (tmp_path / "test-1.jpg").exists()


def test_provider_503_stops_the_pass(tmp_path, monkeypatch):
    import pytest
    import requests
    monkeypatch.setattr(config, "IMAGE_CACHE", tmp_path)

    class Response:
        status_code = 503
        def raise_for_status(self):
            raise requests.HTTPError("unavailable", response=self)

    monkeypatch.setattr(build_index.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(build_index.ProviderUnavailable):
        build_index.download_and_hash({"id": "test-1"}, "https://example.test")


def test_orb_build_stops_before_reading_when_commit_is_low(monkeypatch):
    monkeypatch.setattr(build_orb_db, "available_commit_bytes", lambda: 1024)
    assert build_orb_db.main() == 1
