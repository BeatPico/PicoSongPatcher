"""Focused checks; the integration smoke performs two four-row updates only."""

import importlib.util
import json
from pathlib import Path

import pytest


ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
SPEC = importlib.util.spec_from_file_location("pico_finetune_stages", ENGINE / "finetune_stages.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
DATASET = ROOT / "training_examples/prepared/20261101T192705Z/v2"
BUNDLE = ENGINE / "models/v2_expert"


@pytest.mark.parametrize("arguments", [
    ("intensity", 1, 1e-5, 0), ("positions", 0, 1e-5, 0),
    ("positions", True, 1e-5, 0), ("positions", 1, float("nan"), 0),
    ("positions", 1, 1e-5, -1),
])
def test_reject_invalid_training_arguments(arguments):
    with pytest.raises(ValueError):
        adapter._check_arguments(*arguments)


def test_reject_source_bundle_and_nonempty_destinations(tmp_path):
    source = tmp_path / "bundle/positions"
    source.mkdir(parents=True)
    for destination in (source, source.parent / "arrows", tmp_path):
        with pytest.raises(ValueError, match="outside"):
            adapter._target(destination, source)
    destination = tmp_path / "candidate/positions"
    destination.mkdir(parents=True)
    (destination / "existing").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        adapter._target(destination, source)


def test_frozen_settings_restore_on_failure():
    adapter._imports()
    from tools.config import config

    original = config.positions_history_length
    manifest = {"compatibility": {"architecture": {"history_length": 7}}, "training": {}}
    with pytest.raises(RuntimeError):
        with adapter._frozen_settings("positions", manifest):
            assert config.positions_history_length == 7
            assert config.positions_history_corrupt_rate == 0
            raise RuntimeError("exercise restoration")
    assert config.positions_history_length == original


@pytest.mark.skipif(not DATASET.is_dir() or not BUNDLE.is_dir(), reason="local pretrained fixture absent")
def test_warm_start_two_minibatches_save_earlier_validation_winner(tmp_path, monkeypatch):
    adapter._imports()
    import numpy as np
    from training.pipeline_v2 import runner, settings
    from training.beat_models.artifacts import file_hash

    performance = settings.performance_settings()
    monkeypatch.setattr(settings, "performance_settings", lambda: {
        **performance, "compile_steps": False, "prefetch_batches": 0, "ram_cache_mib": 128})
    validation = iter((.5, .4, .45))
    calls = []

    def tiny_epoch(data, stage, execution, batch_size, *, training=False, **kwargs):
        calls.append("train" if training else "validation")
        execution.reset()
        if training:
            # The upstream reader's split boundary remains authoritative.
            examples = next(runner.batches(data, stage, "train", 4))
            execution.step(examples, training=True)
            value = execution.loss()
        else:
            value = next(validation)
        return value, {"metrics": {}, "examples": 4, "batches": 1}

    monkeypatch.setattr(runner, "_epoch", tiny_epoch)
    source = BUNDLE / "positions"
    source_hash = file_hash(source / "best.keras")
    report = adapter.finetune("positions", DATASET, source, tmp_path / "candidate/positions",
                              epochs=2, log=lambda message: None)
    assert calls == ["validation", "train", "validation", "train", "validation"]
    assert report["optimizer_steps"] == 2
    assert report["best_epoch"] == 1
    assert report["selected_checkpoint"] == "finetuned"
    assert report["checkpoint_load_verified"]
    assert report["source_unchanged"] and source_hash == file_hash(source / "best.keras")
    assert report["checkpoint_sha256"] != source_hash
    assert not set(report["fine_tuning"]["train_song_ids"]) & set(report["fine_tuning"]["validation_song_ids"])
    target_manifest = json.loads((tmp_path / "candidate/positions/manifest.json").read_text())
    original_manifest = json.loads((source / "manifest.json").read_text())
    assert target_manifest["compatibility"] == original_manifest["compatibility"]
    assert target_manifest["vocabularies"] == original_manifest["vocabularies"]
    result = adapter.evaluate("positions", DATASET, tmp_path / "candidate/positions", batch_size=128)
    assert result["optimizer_steps"] == 0 and result["examples"] > 0
    assert np.isfinite(result["loss"])
    assert set(result["heads"]) == {"red", "blue"}
    assert all(0 <= head["accuracy"] <= 1 for head in result["heads"].values())


@pytest.mark.skipif(not DATASET.is_dir() or not BUNDLE.is_dir(), reason="local pretrained fixture absent")
def test_hazards_skip_without_validation_wall_positives(tmp_path):
    destination = tmp_path / "candidate/hazards"
    report = adapter.finetune("hazards", DATASET, BUNDLE / "hazards", destination,
                              epochs=1, log=lambda message: None)
    assert report["status"] == "skipped"
    assert "wall validation has no supervised positive" in report["reason"]
    assert report["optimizer_steps"] == 0
    assert not (destination / "best.keras").exists()
    assert not (destination / "manifest.json").exists()
    assert (destination / "report.json").exists()
