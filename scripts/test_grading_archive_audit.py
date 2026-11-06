import csv
import io
import tarfile

from scripts.audit_grading_archives import audit


def test_archive_audit_finds_missing_and_conflicting_duplicates(tmp_path):
    with (tmp_path / "metadata.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "grade"])
        writer.writeheader()
        writer.writerows([{"filename": "a.jpg", "grade": "8"},
                          {"filename": "b.jpg", "grade": "9"},
                          {"filename": "missing.jpg", "grade": "10"}])
    with tarfile.open(tmp_path / "data_shard_0000.tar", "w") as archive:
        for name, content in (("a.jpg", b"same"), ("b.jpg", b"same"), ("unmatched.png", b"other")):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    (tmp_path / "data_shard_0001.tar.partial").write_bytes(b"unfinished")
    result = audit(tmp_path)
    assert result["completed_archive_files"] == 1
    assert result["images"]["metadata_matched"] == 2
    assert result["images"]["metadata_unmatched"] == 1
    assert result["metadata_names_not_in_downloaded_archives"] == 1
    assert result["duplicate_content_conflicting_grades"] == 1
