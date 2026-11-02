"""Small real CPU warm-start check; no production bundle or dataset is written."""

from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finetune_beats as adapter


@pytest.mark.parametrize("kwargs", [
    {"stage": "positions"}, {"epochs": 0}, {"epochs": True},
    {"learning_rate": 0}, {"learning_rate": float("nan")}, {"seed": -1},
])
def test_rejects_invalid_arguments_before_loading_data(tmp_path, kwargs):
    arguments = dict(stage="intensity", dataset_root=tmp_path / "absent",
                     source_stage=tmp_path / "source", target_stage=tmp_path / "target")
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        adapter.finetune(**arguments)
    assert not (tmp_path / "target").exists()


def test_real_intensity_warm_start_preserves_source_and_split_boundaries(tmp_path):
    artifacts, models, runner, settings = adapter._framework()
    from training.beat_models.__main__ import _dummy_data
    songs, manifest = _dummy_data()
    dataset = tmp_path / "data"
    artifacts.write_dataset(dataset, songs, manifest["normalization"])
    source = artifacts.new_run_directory(tmp_path / "source")
    models.keras.utils.set_random_seed(19)
    with models.tf.device("/CPU:0"):
        model = models.build_model("intensity")
        model.compile(optimizer=models.keras.optimizers.Adam(learning_rate=1e-4),
                      loss=models.intensity_objective)
        train = [song for song in songs if song.metadata["split"] == "train"]
        inputs, target, _ = runner._batch("intensity", runner._references(train, "intensity"), {})
        # Establish an actual trained source before attempting warm-start updates.
        model.train_on_batch(inputs, target)
        with artifacts.posix_gfile_join():
            model.save(source / "best.keras")
    artifacts.write_model_manifest(source, "intensity", source / "best.keras", manifest,
                                   {"status": "trained", "architecture": settings.architecture_settings("intensity"),
                                    "parameters": model.count_params(), "batch_size": 1})
    before = {path.name: artifacts.file_hash(path) for path in source.iterdir()}
    report = adapter.finetune("intensity", dataset, source, tmp_path / "adapted",
                              epochs=1, learning_rate=1e-5, seed=127, log=None)
    assert report["optimizer_steps"] > 0
    assert report["training_weights_changed"]
    assert report["training_weight_delta_l2"] > 0
    assert report["epochs_completed"] == 1
    assert report["best_validation_loss"] <= report["baseline_validation_loss"]
    assert report["source_unchanged"]
    assert before == {path.name: artifacts.file_hash(path) for path in source.iterdir()}
    assert set(report["fine_tuning"]["train_song_ids"]) == set(manifest["splits"]["train"])
    assert not set(report["fine_tuning"]["train_song_ids"]) & set(manifest["splits"]["test"])
    _, output = artifacts.load_model_manifest(tmp_path / "adapted", "intensity", manifest)
    assert output["training"]["architecture"] == artifacts.json_value(settings.architecture_settings("intensity"))
    assert output["normalization_sha256"] == manifest["normalization_sha256"]
    evaluation = adapter.evaluate("intensity", dataset, tmp_path / "adapted", split="test")
    assert np.isfinite(evaluation["loss"])
    assert evaluation["examples"] == len(manifest["splits"]["test"])
    assert evaluation["partial_diagnostic"] is False
    models.keras.backend.clear_session()


@pytest.mark.parametrize("stage", ("intensity", "timing"))
def test_shipped_tf_keras_graph_loads_differentiates_and_cpu_archive_roundtrips(stage, tmp_path, monkeypatch):
    artifacts, models, runner, _ = adapter._framework()
    root = Path(__file__).resolve().parents[1]
    dataset = root / "training_examples/prepared/20261101T192705Z/beat"
    source = root / "ai_engine/models/v2_expert" / stage
    if not dataset.is_dir() or not source.is_dir():
        pytest.skip("Local original bundle and prepared 10-song dataset are required")

    def forbid_deserialization(*args, **kwargs):
        pytest.fail("Cross-runtime archives must restore graph-validated weights, not deserialize modules")

    monkeypatch.setattr(models.keras.models, "load_model", forbid_deserialization)
    original = {name: artifacts.file_hash(source / name) for name in ("manifest.json", "best.keras")}
    songs, manifest = adapter._data(stage, dataset)
    model, _, _ = adapter._load(stage, source, manifest)
    train = [song for song in songs if song.metadata["split"] == "train"]
    references = runner._references(train, stage)[:1]
    profiles = {} if stage == "intensity" else runner._profiles(train)
    batch = runner._batch(stage, references, profiles)
    before = model.get_weights()
    with models.tf.device("/CPU:0"):
        checked = runner._gradient_check(model, stage, batch)
        expected = np.asarray(model(batch[0], training=False))
    assert checked["finite_gradients"]
    assert checked["gradient_variables"] == len(model.trainable_variables)
    for old, current in zip(before, model.get_weights()):
        np.testing.assert_array_equal(old, current)
    # _restore compares the same graph signature used by production V2Runtime.
    output = tmp_path / "cpu.keras"
    with artifacts.posix_gfile_join():
        model.save(output)
    restored = adapter._restore(stage, output)
    np.testing.assert_allclose(restored(batch[0], training=False), expected, atol=1e-6, rtol=1e-6)
    assert original == {name: artifacts.file_hash(source / name) for name in original}
    models.keras.backend.clear_session()
