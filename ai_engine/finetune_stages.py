"""Warm-start downstream v2 stages without modifying the shipped bundle.

Call from the bundled runtime, with InfernoSaber as the working directory.
Imports are inert. Training uses prepared TRAIN rows only; validation chooses
between the unchanged pretrained model (epoch zero) and updated checkpoints.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import sys
from time import perf_counter


STAGES = ("positions", "arrows", "multiplier", "hazards", "arcs")
_ENGINE = Path(__file__).resolve().parent


def _imports():
    root = str(_ENGINE / "infernosaber")
    if root not in sys.path:
        sys.path.insert(0, root)


def _check_arguments(stage, epochs, learning_rate, seed):
    if stage not in STAGES:
        raise ValueError(f"Unsupported downstream fine-tuning stage: {stage}")
    if isinstance(epochs, bool) or not isinstance(epochs, int) or epochs < 1:
        raise ValueError("epochs must be a positive integer")
    if (isinstance(learning_rate, bool) or not isinstance(learning_rate, (int, float))
            or not math.isfinite(learning_rate) or learning_rate <= 0):
        raise ValueError("learning_rate must be finite and positive")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")


def _source(path, stage):
    root = Path(path).resolve()
    if root.is_file():
        root = root.parent
    with (root / "manifest.json").open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("stage") != stage or manifest.get("status") != "trained":
        raise ValueError("Fine-tuning requires an explicitly trained same-stage source")
    return root, manifest


def _target(path, source_root):
    root = Path(path).resolve()
    # The source's containing bundle, and ancestors of the source, are read-only.
    if root.is_relative_to(source_root.parent) or source_root.is_relative_to(root):
        raise ValueError("Fine-tuning destination must be outside the source bundle")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("Fine-tuning destination must be nonexistent or empty")
    return root


def _recordings(manifest, stage):
    from training.pipeline_v2.arc_policy import stage_split

    result = {}
    for split in ("train", "validation"):
        rows = [{key: deepcopy(record.get(key)) for key in
                 ("song_id", "group_id", "audio_sha256", "decoded_sha256")}
                for record in manifest["songs"] if stage_split(manifest, stage, record) == split]
        prefix = "train" if split == "train" else "validation"
        result[f"{prefix}_song_ids"] = [row["song_id"] for row in rows]
        result["training_recordings" if split == "train" else "validation_recordings"] = rows
    return result


@contextmanager
def _frozen_settings(stage, manifest):
    """Resolve historical architecture/defaults in memory, restoring the caller."""
    from tools.config import config

    settings = {f"{stage}_{key}": deepcopy(value) for key, value in
                manifest["compatibility"]["architecture"].items()}
    training = manifest.get("training") or {}
    defaults = {"optimizer": "Adam", "batch_size": 128, "clipnorm": 1.,
                "early_stopping_patience": 10, "early_stopping_min_delta": 0.,
                "shuffle": True, "enabled_song_supervision": False,
                "history_corrupt_rate": 0., "history_corrupt_drop": 1.,
                "history_corrupt_keep_guard": True, "negative_supervision_weight": 1.}
    corruption = training.get("history_corruption") or {}
    for key, default in defaults.items():
        value = training.get(key)
        if value is None and key.startswith("history_corrupt_"):
            value = corruption.get(key.removeprefix("history_corrupt_"))
        settings[f"{stage}_{key}"] = deepcopy(default if value is None else value)
    missing = object()
    before = {key: getattr(config, key, missing) for key in settings}
    try:
        for key, value in settings.items():
            setattr(config, key, value)
        yield
    finally:
        for key, value in before.items():
            if value is missing:
                delattr(config, key)
            else:
                setattr(config, key, value)


def finetune(stage, dataset_root, source_stage, target_stage, epochs=3,
             learning_rate=1e-5, seed=127, log=print):
    """Load pretrained weights, update TRAIN, and save the validation winner.

    ``status=skipped`` means insufficient sparse supervision and emits a report
    only, without a pretend checkpoint. ``selected_checkpoint=pretrained_baseline``
    means real optimizer updates were attempted but validation retained epoch zero.
    No test predictions or test metrics participate in selection.
    """
    _check_arguments(stage, epochs, learning_rate, seed)
    _imports()
    source_root, source = _source(source_stage, stage)
    target = _target(target_stage, source_root)
    with _frozen_settings(stage, source):
        return _finetune(stage, Path(dataset_root).resolve(), source_root, source,
                         target, epochs, float(learning_rate), seed, log)


def _finetune(stage, dataset_root, source_root, source, target, epochs,
              learning_rate, seed, log):
    import numpy as np
    from training.beat_models.artifacts import file_hash, posix_gfile_join, write_json
    from training.pipeline_v2.artifacts import execution_identity, load_dataset
    from training.pipeline_v2.runner import batches, load_stage_artifact, _epoch
    from training.pipeline_v2.settings import performance_settings, training_settings
    from training.pipeline_v2.supervision import class_balance, validate_support
    from training.pipeline_v2.training_data import TrainingData
    from training.pipeline_v2.policies import repaired, split_identity
    from training.pipeline_v2 import models
    from training.pipeline_v2.execution import StageExecution
    from tools.config import config

    started = perf_counter()
    dataset = load_dataset(dataset_root)
    settings = training_settings(stage)
    performance = performance_settings()
    source_manifest_sha = file_hash(source_root / "manifest.json")
    models.keras.utils.set_random_seed(seed)
    # The loader validates the source hash, architecture and frozen dataset
    # encoder/normalization/vocabulary/policy identity before restoring weights.
    model, source = load_stage_artifact(source_root, stage, dataset)
    source_checkpoint = source_root / source["checkpoint"]
    original_weights = model.get_weights()
    log(f"{stage}: pretrained weights loaded; validating prepared split support")
    data = TrainingData(dataset, stage, ram_cache_mib=performance["ram_cache_mib"],
                        require_real=True,
                        enabled_song_supervision=settings["enabled_song_supervision"])
    report = {"kind": "v2_stage_finetuning", "stage": stage, "status": "pending",
              "source_stage": str(source_root), "source_checkpoint_sha256": source["checkpoint_sha256"],
              "source_manifest_sha256": source_manifest_sha,
              "dataset_root": str(dataset_root), "dataset_manifest_sha256": file_hash(dataset_root / "manifest.json"),
              "artifact": str(target), "checkpoint": "best.keras", "seed": seed,
              "epochs_requested": epochs, "learning_rate": learning_rate,
              "batch_size": settings["batch_size"], "optimizer_steps": 0,
              "optimizer": settings["optimizer"], "clipnorm": settings["clipnorm"],
              "splits": data.counts, "training_data": data.report,
              "split_identity": split_identity(dataset.manifest, stage),
              "fine_tuning": _recordings(dataset.manifest, stage),
              "conditioning": "fixed upstream reference",
              "history_conditioning": "strictly-preceding-teacher-forced",
              "selection_split": "validation", "test_used_for_selection": False,
              "baseline_epoch": 0, "best_epoch": 0,
              "compatibility": deepcopy(source["compatibility"]),
              "input_shapes": deepcopy((source.get("training") or {}).get("input_shapes")),
              "bomb_presence": bool((source.get("training") or {}).get("bomb_presence")),
              "frozen_policies": deepcopy(dataset.manifest.get("data_settings", {})),
              "limitations": ["Conditional validation does not measure composed generation or playability.",
                              "No test scores are used to choose checkpoints.",
                              "Changed sparse checkpoints require new validation-only calibration; source calibration is not reusable."],
              **execution_identity()}
    target.mkdir(parents=True, exist_ok=True)
    try:
        if stage in ("hazards", "arcs"):
            validate_support(data.support, stage)
        if any(data.counts[split]["supervised_values"] <= 0 for split in ("train", "validation")):
            raise ValueError("Training requires separately supervised train and validation splits")
    except ValueError as error:
        report.update(status="skipped", reason=str(error), checkpoint=None,
                      selected_checkpoint="source_retained", seconds=perf_counter() - started)
        write_json(target / "report.json", report)
        log(f"{stage}: skipped: {error}")
        return report

    sparse = stage in ("hazards", "arcs") and repaired(dataset.manifest.get("data_settings", {}), stage)
    balance = class_balance(data.support, stage, config.pipeline_v2_positive_weight_cap,
                            settings["negative_supervision_weight"]) if sparse else None
    batch_size = settings["batch_size"]
    preview = next(batches(data, stage, "train", batch_size))
    optimizer = models.keras.optimizers.get({"class_name": settings["optimizer"],
                                            "config": {"learning_rate": learning_rate,
                                                       "clipnorm": settings["clipnorm"]}})
    execution = StageExecution(stage, model, preview, optimizer=optimizer,
                               compiled=performance["compile_steps"], balance=balance,
                               vocabularies=dataset.vocabularies if sparse else None)
    # Source val_pr_auc remains valid only for repaired sparse policies.
    monitor = "val_pr_auc" if sparse and (source.get("training") or {}).get("monitor") == "val_pr_auc" else "val_loss"
    maximize = monitor == "val_pr_auc"
    validation_kwargs = {"prefetch": performance["prefetch_batches"], "verbose": 0}
    val_loss, val_stats = _epoch(data, stage, execution, batch_size, **validation_kwargs)
    baseline_metric = float(val_stats["metrics"]["pr_auc"] if maximize else val_loss)
    if not np.isfinite(baseline_metric):
        raise ValueError("Nonfinite pretrained validation metric")
    best = baseline_metric
    best_loss = val_loss
    selected_weights = original_weights
    shutil.copy2(source_checkpoint, target / "best.keras")
    report.update(monitor=monitor, monitor_mode="max" if maximize else "min",
                  baseline_validation_loss=val_loss, baseline_validation_metrics=val_stats["metrics"],
                  class_balance=balance, selected_checkpoint="pretrained_baseline")
    rng = np.random.default_rng(seed)
    corrupt_rng = np.random.default_rng(np.random.SeedSequence(seed).spawn(1)[0])
    corruption = {"rate": settings["history_corrupt_rate"], "drop": settings["history_corrupt_drop"],
                  "keep_guard": settings["history_corrupt_keep_guard"]}
    report["history_corruption"] = corruption
    history, stale = [], 0
    for epoch in range(1, epochs + 1):
        train_loss, train_stats = _epoch(data, stage, execution, batch_size, training=True,
            shuffle=settings["shuffle"], rng=rng, corruption=corruption,
            corruption_rng=corrupt_rng, **validation_kwargs)
        val_loss, val_stats = _epoch(data, stage, execution, batch_size, **validation_kwargs)
        current = float(val_stats["metrics"]["pr_auc"] if maximize else val_loss)
        if not np.isfinite(current):
            raise ValueError("Nonfinite fine-tuned validation metric")
        delta = settings["early_stopping_min_delta"]
        improved = current > best + delta if maximize else current < best - delta
        history.append({"epoch": epoch, "loss": train_loss, "val_loss": val_loss,
                        "monitored_value": current, "selected": bool(improved),
                        "train": train_stats, "validation": val_stats})
        log(f"{stage}: epoch {epoch}/{epochs} loss={train_loss:.6f} val_loss={val_loss:.6f} {monitor}={current:.6f}" +
            (" selected" if improved else ""))
        if improved:
            best, best_loss, stale = current, val_loss, 0
            selected_weights = model.get_weights()
            report.update(best_epoch=epoch, selected_checkpoint="finetuned", best_validation_metrics=val_stats["metrics"])
            with posix_gfile_join():
                model.save(str(target / "best.keras"))
        else:
            stale += 1
            if stale >= settings["early_stopping_patience"]:
                break
    updated = any(not np.array_equal(old, new) for old, new in zip(original_weights, model.get_weights()))
    if int(optimizer.iterations.numpy()) <= 0 or not updated:
        raise ValueError("Fine-tuning did not apply any effective pretrained weight updates")
    if file_hash(source_checkpoint) != source["checkpoint_sha256"] or file_hash(source_root / "manifest.json") != source_manifest_sha:
        raise ValueError("Source checkpoint/manifest changed during fine-tuning")
    report.update(status="trained", optimizer_steps=int(optimizer.iterations.numpy()),
                  epochs_completed=len(history), history=history,
                  training_weights_changed=True, best_monitored_value=best,
                  best_validation_loss=best_loss, source_unchanged=True,
                  selected_weights_changed=report["best_epoch"] > 0,
                  checkpoint_sha256=file_hash(target / "best.keras"),
                  seconds=perf_counter() - started)
    manifest = deepcopy(source)
    manifest.update(checkpoint="best.keras", checkpoint_sha256=report["checkpoint_sha256"],
                    status="trained", training=report, **execution_identity())
    write_json(target / "manifest.json", manifest)
    # Round-trip through the production-compatible loader, checking selected
    # weights against the in-memory winner or the original epoch-zero weights.
    restored, _ = load_stage_artifact(target, stage, dataset)
    if any(not np.array_equal(a, b) for a, b in zip(selected_weights, restored.get_weights())):
        raise ValueError("Saved selected checkpoint failed its weight round-trip")
    report["checkpoint_load_verified"] = True
    write_json(target / "report.json", report)
    log(f"{stage}: finished; selected {report['selected_checkpoint']} epoch {report['best_epoch']}")
    return report


def evaluate(stage, dataset_root, artifact_stage, split="test", batch_size=128):
    """Score a prepared held-out split without optimizing or selecting anything.

    These fixed upstream/reference-history scores complement a production A/B
    generation comparison. This helper does not claim curated-corpus coverage.
    """
    _check_arguments(stage, 1, 1e-5, 0)
    if split not in ("validation", "test"):
        raise ValueError("Evaluation requires a held-out validation or test split")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    _imports()
    root, manifest = _source(artifact_stage, stage)
    with _frozen_settings(stage, manifest):
        import numpy as np
        from training.pipeline_v2.artifacts import load_dataset
        from training.pipeline_v2.runner import batches, load_stage_artifact
        from training.pipeline_v2.models import loss_components, combine_loss_components
        from training.pipeline_v2.evaluation import _head_scores, _add_scores, _summarize_heads

        dataset = load_dataset(dataset_root)
        model, manifest = load_stage_artifact(root, stage, dataset)
        totals, heads, count = {}, {}, 0
        for examples in batches(dataset, stage, split, batch_size):
            predicted = model(examples.inputs, training=False)
            components = loss_components(stage, examples.targets, predicted, examples.weights)
            for head, pair in components.items():
                totals.setdefault(head, np.zeros(2, dtype=np.float64))[:] += [float(value) for value in pair]
            for head, values in examples.targets.items():
                _add_scores(heads.setdefault(head, {}), _head_scores(
                    stage, head, values, np.asarray(predicted[head]), examples.weights[head], .5))
            count += len(examples.times)
        if not count:
            raise ValueError(f"No {stage} examples in {split}")
        return {"kind": "v2_finetuning_conditional_evaluation", "stage": stage,
                "evaluated_split": split, "optimizer_steps": 0, "examples": count,
                "checkpoint_sha256": manifest["checkpoint_sha256"],
                "loss": float(combine_loss_components(stage, totals)),
                "heads": _summarize_heads(heads),
                "conditioning": "fixed upstream reference and strictly preceding own-stage reference history",
                "limitations": ["Conditional reference agreement does not establish composed generation or playability.",
                                "Unknown/unscheduled source objects remain outside the masked conditional objective."]}
