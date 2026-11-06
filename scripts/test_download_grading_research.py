import hashlib

from scripts import download_grading_research as downloader


def test_resumes_and_checksums_archive(tmp_path, monkeypatch):
    content = b"abcdefghij"
    target = tmp_path / "data_shard_0000.tar"
    (tmp_path / "data_shard_0000.tar.partial").write_bytes(content[:3])
    monkeypatch.setattr(downloader, "ROOT", tmp_path)
    monkeypatch.setattr(downloader, "CHUNK", 4)

    class Response:
        def __init__(self, block=b"", headers=None):
            self.content = block
            self.headers = headers or {}
            self.status_code = 206

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.content

        def json(self):
            return [{"path": target.name, "size": len(content),
                     "lfs": {"oid": hashlib.sha256(content).hexdigest()}}]

    class Session:
        def get(self, url, **kwargs):
            if "/api/datasets/" in url:
                return Response()
            start, end = map(int, kwargs["headers"]["Range"][6:].split("-"))
            return Response(content[start:end + 1],
                            {"Content-Range": f"bytes {start}-{end}/{len(content)}"})

    monkeypatch.setattr(downloader.requests, "Session", Session)
    downloader.main()
    assert target.read_bytes() == content
    assert not (tmp_path / "data_shard_0000.tar.partial").exists()
