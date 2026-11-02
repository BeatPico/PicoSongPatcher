"""Warm-start the existing beat models without changing the bundled trainers.

Only explicit calls train. Inputs, normalization, losses and checkpoint manifests
use InfernoSaber's existing contracts. The source checkpoint is also a validation
candidate (epoch zero); test songs never influence updates or checkpoint choice.
"""

from copy import deepcopy
import json
import math
from numbers import Integral, Real
from pathlib import Path
import shutil
import sys
from time import perf_counter
import zipfile


def _framework():
    # This adapter can be imported either as ai_engine.finetune_beats or directly
    # from the ai_engine directory. Do not change cwd, environment or config.
    framework = str(Path(__file__).resolve().parent / "infernosaber")
    if framework not in sys.path:
        sys.path.insert(0, framework)
    from training.beat_models import artifacts, models, runner, settings
    return artifacts, models, runner, settings


def _positive_integer(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _stage(stage):
    if stage not in ("intensity", "timing"):
        raise ValueError("Only intensity and timing are supported")


def _data(stage, dataset_root):
    artifacts, _, runner, _ = _framework()
    from training.beat_models.training_data import load_training_dataset
    songs, manifest = load_training_dataset(dataset_root, stage, verbose=0)
    return runner._normalized(songs, manifest), manifest


def _restore(stage, checkpoint):
    """Use production's validated graph/weight bridge for GPU tf-keras files."""
    artifacts, models, _, _ = _framework()
    from evaluation.beat_pair import _graph_signature
    with zipfile.ZipFile(checkpoint) as archive:
        saved = json.loads(archive.read("config.json"))
    with models.tf.device("/CPU:0"):
        model = models.build_model(stage)
        if _graph_signature(saved) != _graph_signature(json.loads(model.to_json())):
            raise ValueError(f"Unsupported {stage} checkpoint graph")
        with artifacts.posix_gfile_join():
            model.load_weights(checkpoint)
    return model


def _load(stage, model_stage, manifest):
    artifacts, models, _, settings = _framework()
    checkpoint, source = artifacts.load_model_manifest(model_stage, stage, manifest)
    if source.get("status") != "trained" or source.get("training", {}).get("status", "trained") != "trained":
        raise ValueError("Warm-start/evaluation requires a trained checkpoint")
    architecture = artifacts.json_value(settings.architecture_settings(stage))
    if source.get("training", {}).get("architecture") != architecture:
        raise ValueError("Source checkpoint architecture disagrees with the active production contract")
    model = _restore(stage, checkpoint)
    if model.count_params() != source.get("training", {}).get("parameters", model.count_params()):
        raise ValueError("Loaded parameter count disagrees with the source manifest")
    return model, checkpoint, source


def _batch_size(value, source, settings):
    return _positive_integer(source.get("training", {}).get("batch_size", settings["batch_size"])
                             if value is None else value, "batch_size")


def finetune(stage, dataset_root, source_stage, target_stage, epochs=3,
             learning_rate=1e-5, seed=127, log=print, *, batch_size=None):
    """Train all source-model layers on train only; select by validation loss.

    ``target_stage`` must not exist and must be outside ``source_stage``. A fresh
    optimizer is intentional; this is low-rate adaptation, not optimizer resume.
    Timing preserves source reference/noisy-reference conditioning. Predicted
    conditioning requires a separate upstream artifact and is rejected here.
    Returns the report also saved as ``target_stage/report.json``.
    """
    _stage(stage)
    epochs = _positive_integer(epochs, "epochs")
    seed = _positive_integer(seed, "seed", 0)
    if (isinstance(learning_rate, bool) or not isinstance(learning_rate, Real)
            or not math.isfinite(learning_rate) or learning_rate <= 0):
        raise ValueError("learning_rate must be finite and positive")
    source_root = Path(source_stage).resolve()
    source_root = source_root.parent if source_root.is_file() else source_root
    destination = Path(target_stage).resolve()
    if destination == source_root or source_root in destination.parents:
        raise ValueError("The target must be outside the source stage")
    if destination.exists():
        raise FileExistsError(f"Fine-tuning target already exists: {destination}")
    artifacts, models, runner, settings = _framework()
    import numpy as np
    from training.beat_models.training_input import BatchSource
    started = perf_counter()
    songs, manifest = _data(stage, dataset_root)
    train = [song for song in songs if song.metadata["split"] == "train"]
    validation = [song for song in songs if song.metadata["split"] == "validation"]
    if not train or not validation:
        raise ValueError("Separate nonempty train and validation splits are required")
    train_refs, validation_refs = runner._references(train, stage), runner._references(validation, stage)
    if not train_refs or not validation_refs:
        raise ValueError("Train and validation need valid stage targets")
    models.keras.utils.set_random_seed(seed)
    model, source_checkpoint, source = _load(stage, source_stage, manifest)
    source_weights = model.get_weights()
    defaults = settings.training_settings(stage)
    source_training = source["training"]
    size = _batch_size(batch_size, source, defaults)
    conditioning = "reference" if stage == "intensity" else source_training.get("conditioning", "reference")
    if conditioning not in ("reference", "noisy-reference"):
        raise ValueError("This adapter requires reference or noisy-reference source conditioning")
    noise_std = source_training.get("intensity_noise_std", defaults["intensity_noise_std"])
    train_source = BatchSource(stage, train, train_refs, size, mode=conditioning,
                               shuffle=True, seed=seed, noise_std=noise_std)
    valid_source = BatchSource(stage, validation, validation_refs, size,
                               mode="reference", shuffle=False, seed=seed)
    train_data, valid_data = train_source.dataset(1), valid_source.dataset(1)
    source_manifest_hash = artifacts.file_hash(source_root / "manifest.json")
    source_checkpoint_hash = artifacts.file_hash(source_checkpoint)
    report = {
        "kind": "beat_model_finetune", "stage": stage, "status": "trained", "warm_start": True,
        "source_stage": str(source_root), "source_checkpoint_sha256": source_checkpoint_hash,
        "source_manifest_sha256": source_manifest_hash,
        "warm_start_loading": "production graph-signature validation then load_weights",
        "adapter_sha256": artifacts.file_hash(Path(__file__)),
        "dataset_root": str(Path(dataset_root).resolve()),
        "dataset_manifest_sha256": artifacts.file_hash(Path(dataset_root) / "manifest.json"),
        "architecture": deepcopy(source_training["architecture"]),
        "parameters": model.count_params(), "epochs": epochs, "learning_rate": float(learning_rate),
        "seed": seed, "batch_size": size, "optimizer": source_training.get("optimizer", "Adam"),
        "clipnorm": source_training.get("clipnorm", defaults["clipnorm"]),
        "conditioning": conditioning if stage == "timing" else None,
        "validation_conditioning": "reference", "intensity_noise_std": float(noise_std),
        "monitor": "val_loss", "monitor_mode": "min", "baseline_is_candidate": True,
        "optimizer_state": "fresh", "all_model_layers_trainable": True,
        "train_examples": len(train_refs), "validation_examples": len(validation_refs),
        "fine_tuning": {
            "train_song_ids": [song.song_id for song in train],
            "validation_song_ids": [song.song_id for song in validation],
            "training_recordings": [{"song_id": song.song_id,
                                     **{key: song.metadata.get(key) for key in
                                        ("group_id", "audio_sha256", "decoded_sha256")}}
                                    for song in train],
        },
        "splits": deepcopy(manifest["splits"]),
        "recording_groups": {split: sorted({song.metadata["group_id"] for song in songs
                                            if song.metadata["split"] == split})
                             for split in ("train", "validation", "test")},
        "training_input": manifest.get("training_input"),
        "sampling": "all valid songs" if stage == "intensity" else "all nonoverlapping valid timing cores",
        "selection_scope": "validation only; test data never used for training or selection",
        "epoch_seconds": [],
    }
    root = artifacts.new_run_directory(destination)
    checkpoint = root / "best.keras"
    shutil.copy2(source_checkpoint, checkpoint)
    model.trainable = True
    optimizer = models.keras.optimizers.get({"class_name": report["optimizer"], "config": {
        "learning_rate": float(learning_rate), "clipnorm": report["clipnorm"]}})
    objective = models.intensity_objective if stage == "intensity" else models.timing_objective
    model.compile(optimizer=optimizer, loss=objective, run_eagerly=False, steps_per_execution=1)
    with models.tf.device("/CPU:0"):
        baseline = float(model.evaluate(valid_data, verbose=0))
    if not math.isfinite(baseline):
        raise ValueError("Source validation loss is nonfinite")
    report["baseline_validation_loss"] = baseline
    if log:
        log(f"{stage}: warm start, {len(train_refs)} train / {len(validation_refs)} validation examples, "
            f"source validation loss={baseline:.6f}")

    class SelectBest(models.keras.callbacks.Callback):
        def __init__(self):
            super().__init__()
            self.best, self.epoch = baseline, 0

        def on_epoch_begin(self, epoch, logs=None):
            self.started = perf_counter()

        def on_epoch_end(self, epoch, logs=None):
            values = logs or {}
            loss = float(values.get("val_loss", float("nan")))
            if not math.isfinite(loss) or not math.isfinite(float(values.get("loss", float("nan")))):
                raise ValueError("Training produced a nonfinite loss")
            if loss < self.best:
                self.best, self.epoch = loss, epoch + 1
                with artifacts.posix_gfile_join():
                    self.model.save(checkpoint)
            report["epoch_seconds"].append(perf_counter() - self.started)
            if log:
                log(f"{stage}: epoch {epoch + 1}/{epochs}, loss={float(values['loss']):.6f}, "
                    f"val_loss={loss:.6f}, selected_epoch={self.epoch}")
            if epoch + 1 < epochs:
                train_source.prepare_epoch()

    selector = SelectBest()
    with models.tf.device("/CPU:0"):
        history = model.fit(train_data, validation_data=valid_data, epochs=epochs,
                            callbacks=[selector], shuffle=False, verbose=0)
    selected_model = _restore(stage, checkpoint)

    def weight_delta(current):
        return math.sqrt(sum(float(np.square(value.astype(np.float64) - original).sum())
                             for original, value in zip(source_weights, current)))

    training_delta = weight_delta(model.get_weights())
    selected_delta = weight_delta(selected_model.get_weights())
    report.update({"optimizer_steps": int(optimizer.iterations.numpy()),
                   "epochs_completed": len(history.history["loss"]), "history": history.history,
                   "best_epoch": selector.epoch, "best_validation_loss": selector.best,
                   "training_weights_changed": training_delta > 0,
                   "training_weight_delta_l2": training_delta,
                   "selected_weights_changed": selected_delta > 0,
                   "selected_weight_delta_l2": selected_delta,
                   "seconds": perf_counter() - started})
    if report["optimizer_steps"] <= 0:
        raise ValueError("Fine-tuning performed no optimizer updates")
    report["source_unchanged"] = (
        artifacts.file_hash(source_checkpoint) == source_checkpoint_hash
        and artifacts.file_hash(source_root / "manifest.json") == source_manifest_hash)
    if not report["source_unchanged"]:
        raise ValueError("Source checkpoint or manifest changed during training")
    artifacts.write_model_manifest(root, stage, checkpoint, manifest, report)
    # Re-read the public artifact contract before reporting a successful run.
    artifacts.load_model_manifest(root, stage, manifest)
    artifacts.write_json(root / "report.json", report)
    return artifacts.json_value(report)


def evaluate(stage, dataset_root, model_stage, split="test", *, batch_size=None,
             conditioning="reference", intensity_stage=None):
    """Evaluate every example in one fixed split, without fitting anything.

    Reference conditioning is a stage diagnostic. Pass ``conditioning='predicted'``
    and an explicit ``intensity_stage`` for timing conditioned on intensity-model
    outputs. Record the same mode when comparing before/after checkpoints.
    """
    _stage(stage)
    if split not in ("train", "validation", "test"):
        raise ValueError("Evaluation requires one explicit train/validation/test split")
    if conditioning not in ("reference", "predicted"):
        raise ValueError("Evaluation conditioning must be reference or predicted")
    if stage == "intensity" and conditioning != "reference":
        raise ValueError("Intensity has no upstream conditioning")
    artifacts, models, runner, settings = _framework()
    import numpy as np
    songs, manifest = _data(stage, dataset_root)
    selected = [song for song in songs if song.metadata["split"] == split]
    if not selected:
        raise ValueError(f"No songs in evaluation split: {split}")
    model, checkpoint, source = _load(stage, model_stage, manifest)
    size = _batch_size(batch_size, source, settings.training_settings(stage))
    upstream = None
    if stage == "timing" and conditioning == "predicted":
        if intensity_stage is None:
            raise ValueError("Predicted timing conditioning requires intensity_stage")
        upstream, _, _ = _load("intensity", intensity_stage, manifest)
    profiles = {} if stage == "intensity" else runner._profiles(selected, conditioning, upstream)
    references = runner._references(selected, stage)
    if not references:
        raise ValueError("Evaluation split has no valid stage targets")
    reports, losses, weights = [], [], []
    components = np.zeros(4, dtype=np.float64)
    outputs = {song.song_id: (np.zeros(len(song.arrays["frame_times"]), dtype=np.int32),
                              np.zeros(len(song.arrays["frame_times"]), dtype=np.float32),
                              np.zeros(len(song.arrays["frame_times"]), dtype=bool))
               for song in selected} if stage == "timing" else {}
    with models.tf.device("/CPU:0"):
        for offset in range(0, len(references), size):
            chosen = references[offset:offset + size]
            inputs, target, mask = runner._batch(stage, chosen, profiles)
            predicted = model(inputs, training=False)
            losses.append(float(runner._loss(stage, target, predicted, mask, inputs)))
            weights.append(len(chosen))
            probabilities = np.asarray(predicted)
            if not np.isfinite(probabilities).all():
                raise ValueError("Checkpoint produced nonfinite predictions")
            if stage == "intensity":
                for row, (song, _) in enumerate(chosen):
                    truth = song.arrays["intensity"]
                    error = probabilities[row, :len(truth)] - truth
                    count = song.metadata.get("retained_colored_note_count")
                    reports.append({"song_id": song.song_id, "split": split,
                                    "share_mae": float(np.abs(error).mean()),
                                    "share_total_variation": float(np.abs(error).sum() / 2),
                                    "bin_count_mae": float(np.abs(error * count).mean()) if count is not None else None})
            else:
                components += [float(value) for value in models.timing_loss_components(target, predicted, mask)]
                for row, (song, start) in enumerate(chosen):
                    local = np.flatnonzero(mask[row])
                    frames = start + local
                    counts, expected, valid = outputs[song.song_id]
                    counts[frames] = probabilities[row, local].argmax(axis=-1)
                    expected[frames] = probabilities[row, local, 1] + 2 * probabilities[row, local, 2]
                    valid[frames] = True
    report = {"kind": "beat_stage_evaluation", "stage": stage, "split": split,
              "model_stage": str(Path(model_stage).resolve()), "checkpoint_sha256": artifacts.file_hash(checkpoint),
              "dataset_manifest_sha256": artifacts.file_hash(Path(dataset_root) / "manifest.json"),
              "normalization_sha256": manifest["normalization_sha256"],
              "batch_size": size, "conditioning": conditioning,
              "intensity_stage": str(Path(intensity_stage).resolve()) if intensity_stage else None,
              "examples": len(references), "partial_diagnostic": False,
              "recording_groups": sorted({song.metadata["group_id"] for song in selected}),
              "keras_batch_weighted_loss": float(np.average(losses, weights=weights)),
              "loss": float(np.average(losses, weights=weights))}
    if stage == "timing":
        ce_sum, frames, huber_sum, chunks = components
        ce, huber = ce_sum / frames, huber_sum / chunks
        reports = [runner._timing_stage_scores(song, *outputs[song.song_id]) for song in selected]
        report.update({"cross_entropy": float(ce), "normalized_count_huber": float(huber),
                       "loss": float(ce + source["training"]["architecture"]["count_loss_weight"] * huber),
                       "summary": runner._timing_stage_summary(reports)})
    else:
        from training.beat_models.training_metrics import intensity_reference
        reference = intensity_reference(selected, models.keras.backend.epsilon())
        report.update({"target_entropy": reference["target_entropy"],
                       "kl_divergence": max(0.0, report["loss"] - reference["target_entropy"]),
                       "mean_share_mae": float(np.mean([item["share_mae"] for item in reports])),
                       "mean_share_total_variation": float(np.mean([item["share_total_variation"] for item in reports]))})
    report["songs"] = reports
    return artifacts.json_value(report)
