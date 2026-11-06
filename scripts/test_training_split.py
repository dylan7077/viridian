"""Check that synthetic variants never cross the training/validation boundary."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.train_grader import split_by_base


def test_split_keeps_base_images_together():
    items = [(f"synth_front_{i:064x}_{variant}.jpg", [5] * 4)
             for i in range(10) for variant in range(3)]
    train, validation = split_by_base(items)
    base_ids = lambda rows: {Path(path).stem.rsplit('_', 1)[0] for path, _ in rows}
    assert train and validation
    assert not base_ids(train) & base_ids(validation)
    assert len(train) + len(validation) == len(items)
    assert split_by_base(items) == (train, validation)


def test_legacy_identity_is_rejected():
    try:
        split_by_base([("synth_front_00001_0.jpg", [5] * 4)])
    except ValueError as exc:
        assert "Regenerate" in str(exc)
    else:
        raise AssertionError("legacy source IDs must not be accepted")
