import pytest

from scripts.evaluate_centering_dataset import evaluate


def test_missing_photos_are_counted_without_accuracy_claim(tmp_path):
    manifest = tmp_path / "measurements.csv"
    manifest.write_text("annotation_id,left_right_pct_left_share,top_bottom_pct_top_share\n123,48,52\n")
    result = evaluate(manifest, tmp_path / "photos")
    assert result["rows"] == result["missing"] == 1
    assert result["compared"] == 0
    assert "lr_mean_abs_error_pp" not in result


def test_ambiguous_annotation_photo_is_rejected(tmp_path):
    manifest = tmp_path / "measurements.csv"
    manifest.write_text("annotation_id,left_right_pct_left_share,top_bottom_pct_top_share\n123,48,52\n")
    (tmp_path / "123.jpg").write_bytes(b"a")
    (tmp_path / "123.png").write_bytes(b"b")
    with pytest.raises(ValueError, match="Multiple image files"):
        evaluate(manifest, tmp_path)
