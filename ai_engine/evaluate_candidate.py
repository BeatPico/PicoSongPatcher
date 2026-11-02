"""Frozen, held-out CPU comparison of explicit production bundles.

No fitting, checkpoint selection or decoder tuning happens here. The reference
charts supply evaluation labels and the same requested NPS to both bundles;
inference receives only the complete source audio and source BPM.
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
import html
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import zipfile

ENGINE = Path(__file__).resolve().parent
APP = ENGINE.parent
STAGES = ("intensity", "timing", "positions", "arrows", "multiplier", "hazards", "arcs")


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _ffmpeg():
    return Path(os.environ.get("PICO_TRAINING_FFMPEG", APP / "assets" / "tools" / "ffmpeg.exe"))


def _ordinary(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Unsupported evaluation report value: {type(value).__name__}")


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                     default=_ordinary, allow_nan=False) + "\n", encoding="utf-8")


@contextmanager
def _environment():
    """Imports and cwd changes belong to the explicit call, never module import."""
    previous = Path.cwd()
    for path in (ENGINE, ENGINE / "infernosaber"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "2")
    os.environ.setdefault("TF_NUM_INTEROP_THREADS", "2")
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("NUMBA_CACHE_DIR", str(ENGINE / "cache" / "numba"))
    os.chdir(ENGINE / "infernosaber")
    try:
        from ffmpeg_source import install
        install(_ffmpeg())
        yield
    finally:
        os.chdir(previous)


def _identity_tokens(record):
    return {("group" if key == "group_id" else "audio", str(record[key]))
            for key in ("group_id", "audio_sha256", "decoded_sha256") if record.get(key)}


def _split_audit(beat_manifest, v2_manifest):
    """Cross-check recording aliases across both datasets, including train/val."""
    identities, by_id = {}, {}
    for record in beat_manifest["songs"]:
        meta = record["metadata"]
        split, song_id = meta.get("split"), record["song_id"]
        if split not in ("train", "validation", "test") or not meta.get("group_id"):
            raise ValueError("Evaluation requires explicit recording-group splits")
        if song_id in by_id:
            raise ValueError("Duplicate prepared chart ID")
        by_id[song_id] = meta
        for token in _identity_tokens(meta):
            if identities.get(token, split) != split:
                raise ValueError("A recording identity crosses held-out dataset splits")
            identities[token] = split
    expected = {split: [r["song_id"] for r in beat_manifest["songs"]
                        if r["metadata"]["split"] == split]
                for split in ("train", "validation", "test")}
    if beat_manifest.get("splits") != expected:
        raise ValueError("Beat split index does not match its chart metadata")
    if set(by_id) != {r["song_id"] for r in v2_manifest["songs"]}:
        raise ValueError("Beat and v2 datasets do not contain the same charts")
    for record in v2_manifest["songs"]:
        meta = by_id[record["song_id"]]
        for key in ("split", "group_id", "audio_sha256", "decoded_sha256"):
            if record.get(key) != meta.get(key):
                raise ValueError(f"Beat/v2 dataset identity mismatch: {key}")
    test = [record for record in beat_manifest["songs"] if record["metadata"]["split"] == "test"]
    if not test:
        raise ValueError("There are no held-out test charts")
    return {"status": "verified_disjoint_recording_aliases", "split_charts": {k: len(v) for k, v in expected.items()},
            "split_recordings": {s: len({m["group_id"] for m in by_id.values() if m["split"] == s})
                                 for s in expected}, "test_song_ids": [r["song_id"] for r in test]}


def _candidate_audit(base, candidate, beat_manifest):
    """Audit optimization evidence independently of inherited source manifests."""
    by_id = {r["song_id"]: r["metadata"] for r in beat_manifest["songs"]}
    test_ids = {song_id for song_id, meta in by_id.items() if meta["split"] == "test"}
    test_tokens = set().union(*(_identity_tokens(by_id[song_id]) for song_id in test_ids))
    result = {}
    for stage in STAGES:
        original, proposed = base[stage], candidate[stage]
        changed = original["checkpoint_sha256"] != proposed["checkpoint_sha256"]
        fine = proposed.get("training", {}).get("fine_tuning")
        if not fine:
            if changed:
                raise ValueError(f"Changed {stage} has no explicit fine-tuning provenance")
            result[stage] = {"checkpoint_changed": False, "fine_tuning": "none"}
            continue
        train, validation = fine.get("train_song_ids", []), fine.get("validation_song_ids", [])
        if not train or any(song_id not in by_id or by_id[song_id]["split"] != "train" for song_id in train):
            raise ValueError(f"{stage} fine-tuning used unknown or non-training charts")
        if any(song_id not in by_id or by_id[song_id]["split"] != "validation" for song_id in validation):
            raise ValueError(f"{stage} checkpoint selection used unknown or non-validation charts")
        if test_ids.intersection(train) or test_ids.intersection(validation):
            raise ValueError(f"{stage} test charts entered optimization/checkpoint selection")
        recordings = fine.get("training_recordings", [])
        if not recordings or {r["song_id"] for r in recordings} != set(train):
            raise ValueError(f"{stage} has incomplete training recording provenance")
        for recording in recordings + fine.get("validation_recordings", []):
            song_id = recording.get("song_id")
            if song_id not in by_id or _identity_tokens(recording) != _identity_tokens(by_id[song_id]):
                raise ValueError(f"{stage} recording evidence disagrees with prepared identities")
            if test_tokens.intersection(_identity_tokens(recording)):
                raise ValueError(f"{stage} fine-tuning recording aliases overlap test audio")
        result[stage] = {"checkpoint_changed": changed, "train_charts": len(train),
                         "validation_charts": len(validation), "test_overlap": False,
                         "train_recordings": len({r["group_id"] for r in recordings})}
    return result


def _verified_sources(metadata):
    selected = {}
    for role in ("info", "beatmap", "audio"):
        records = [r for r in metadata.get("sources", []) if r.get("role") == role]
        if len(records) != 1:
            raise ValueError(f"Expected exactly one source {role}")
        record = records[0]
        path = Path(record["path"]).resolve()
        if not path.is_file() or _hash(path) != record.get("sha256"):
            raise ValueError(f"Original source changed or is missing: {path}")
        selected[role] = path
    info = _read(selected["info"])
    if not str(info.get("_version", "")).startswith("2."):
        raise ValueError("Review export requires the source Info.dat v2 metadata")
    if float(info["_beatsPerMinute"]) != float(metadata["bpm"]):
        raise ValueError("Source BPM disagrees with prepared metadata")
    provenance = metadata.get("provenance", {})
    if not provenance.get("metadata_verified") or provenance.get("characteristic") != "Standard":
        raise ValueError("Test source lacks verified Standard-chart provenance")
    return selected, info


def _reseed(seed, config):
    import numpy as np
    import tensorflow as tf
    random.seed(seed)
    np.random.seed(seed)
    tf.keras.utils.set_random_seed(seed)
    config.random_seed = seed
    for key in list(vars(config)):
        if key.endswith("_random_seed"):
            setattr(config, key, seed)


def _configure(bundle, config):
    config.v2_model_bundle_path = str(bundle)
    config.v1_lighting_model_path = str(bundle / "v1_lighting" / "tf_event_gen_2_12_5_5__22_18.h5")
    config.v1_lighting_vocabulary_path = str(bundle / "v1_lighting" / "onehot_encoder_events.pkl")
    config.v2_arrows_variety = 0.
    config.v2_inference_threads = 2
    config.v2_timing_batch_size = 8
    config.v2_calm_effects = False
    config.add_bombs_flag = False
    config.add_obstacle_flag = False
    config.add_slider_flag = True
    config.bs_mapping_version = "v3"
    config.create_expert_flag = False


def _chart_metrics(song, chart, requested_nps):
    import numpy as np
    from training.beat_models.contracts import decode_note
    from training.beat_models.metrics import match_onsets, timing_scores
    notes = chart["colorNotes"]  # BPM60 internal chart: b is elapsed seconds.
    duration = float(song.metadata["duration"])
    times = np.unique([n["b"] for n in notes])
    reference = song.arrays["event_times"]
    reference_count = int(np.count_nonzero(song.arrays["note_classes"]))
    bins = len(song.arrays["bin_durations"])
    counts = np.zeros(bins, dtype=np.int64)
    groups = {}
    for note in notes:
        counts[min(int(note["b"]), bins - 1)] += 1
        groups.setdefault(note["b"], {})[note["c"]] = note
    pairs = match_onsets(reference, times, .020)
    hands = {}
    for hand, name in enumerate(("red", "blue")):
        compared = position = direction = presence = 0
        for left, right in pairs:
            target = int(song.arrays["note_classes"][left, hand])
            note = groups[times[right]].get(hand)
            presence += bool(target) == bool(note)
            if target and note:
                expected = decode_note(target)
                compared += 1
                position += expected[:2] == (note["x"], note["y"])
                direction += expected[2] == note["d"]
        hands[name] = {"matched_notes": compared,
                       "presence_accuracy": presence / len(pairs) if len(pairs) else None,
                       "position_accuracy": position / compared if compared else None,
                       "direction_accuracy": direction / compared if compared else None}
    quick = repeated = transitions = 0
    previous = {}
    for note in sorted(notes, key=lambda n: n["b"]):
        old = previous.get(note["c"])
        if old:
            gap = note["b"] - old["b"]
            quick += gap < .12
            if 0 < gap <= 1.:
                transitions += 1
                repeated += old["d"] == note["d"] and note["d"] != 8
        previous[note["c"]] = note
    cells = Counter((round(n["b"], 6), n["x"], n["y"]) for n in notes)
    emitted = counts / len(notes) if notes else np.zeros(bins)
    valid = song.metadata.get("intensity_target_valid", reference_count > 0)
    return {"duration_seconds": duration, "requested_nps": requested_nps,
            "generated_colored_notes": len(notes), "reference_retained_notes": reference_count,
            "reference_original_notes": len(song.arrays["original_notes"]),
            "generated_nps": len(notes) / duration,
            "requested_density_error_nps": len(notes) / duration - requested_nps,
            "absolute_requested_density_error_nps": abs(len(notes) / duration - requested_nps),
            "note_count_error": len(notes) - reference_count,
            "timing_20ms": timing_scores(reference, times, .020),
            "timing_50ms": timing_scores(reference, times, .050),
            "emitted_intensity_share_tv": float(np.abs(emitted - song.arrays["intensity"]).sum() / 2) if valid else None,
            "hands_at_matched_20ms_onsets": hands,
            "structural_proxies": {"same_hand_gaps_under_120ms": quick,
                "same_direction_transitions_within_1s": repeated,
                "same_hand_transitions_within_1s": transitions,
                "exact_same_cell_time_collisions": sum(n - 1 for n in cells.values() if n > 1),
                "note_time_out_of_audio_bounds": sum(not 0 <= n["b"] < duration for n in notes)},
            "objects": {key: len(chart.get(key, [])) for key in
                        ("sliders", "burstSliders", "bombNotes", "obstacles", "basicBeatmapEvents")}}


def _native_chart(chart, bpm):
    result = deepcopy(chart)
    factor = bpm / 60.
    for family in ("colorNotes", "bombNotes", "obstacles", "sliders", "burstSliders", "basicBeatmapEvents", "colorBoostBeatmapEvents"):
        for record in result.get(family, []):
            for key in ("b", "tb") + (("d",) if family == "obstacles" else ()):
                if key in record:
                    record[key] *= factor
    return result


def _review_package(destination, arm, references, charts):
    """Keep source audio byte-for-byte and source Info 2 presentation settings."""
    destination.mkdir(parents=True)
    first = references[0]
    info = deepcopy(first["info"])
    info["_songName"] = f'{info["_songName"]} [Pico {arm.upper()}]'
    info["_levelAuthorName"] = f'InfernoSaber {arm}; source: {info.get("_levelAuthorName", "")}'
    entries = []
    for reference in references:
        difficulty = reference["difficulty"]
        source_entries = [entry for group in reference["info"]["_difficultyBeatmapSets"]
                          if group["_beatmapCharacteristicName"] == "Standard"
                          for entry in group["_difficultyBeatmaps"] if entry["_difficulty"] == difficulty]
        if len(source_entries) != 1:
            raise ValueError("Review chart difficulty has no unique source metadata entry")
        entry = deepcopy(source_entries[0])
        filename = difficulty + "Standard.dat"
        entry["_beatmapFilename"] = filename
        entries.append(entry)
        _write(destination / filename, _native_chart(charts[reference["song"].song_id], float(info["_beatsPerMinute"])))
    info["_difficultyBeatmapSets"] = [{"_beatmapCharacteristicName": "Standard", "_difficultyBeatmaps": entries}]
    # Elapsed-second generation already incorporates the recording timeline.
    info["_songTimeOffset"] = 0.
    audio_name = Path(info["_songFilename"]).name
    cover_name = Path(info["_coverImageFilename"]).name
    if audio_name != info["_songFilename"] or cover_name != info["_coverImageFilename"]:
        raise ValueError("Source review asset names must be local basenames")
    shutil.copyfile(first["sources"]["audio"], destination / audio_name)
    cover = first["sources"]["info"].parent / cover_name
    if not cover.is_file():
        raise ValueError("Original review cover is missing")
    shutil.copyfile(cover, destination / cover_name)
    _write(destination / "Info.dat", info)
    archive = destination.with_suffix(".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(destination.iterdir()):
            package.write(path, path.name)
    return {"folder": str(destination), "zip": str(archive), "zip_sha256": _hash(archive),
            "audio_sha256": _hash(destination / audio_name), "complete_source_audio": True,
            "info_version": info["_version"], "beatmap_version": "3.3.0", "bpm": info["_beatsPerMinute"]}


def _generate_arm(arm, bundle, references, output, seed, log):
    import tensorflow as tf
    from tools.config import config
    from map_creation.v2_runtime import V2Runtime
    from map_creation.v2_generation import scoped_settings, _difficulty_settings
    from map_creation.v2_export import serialize_beatmap
    from full_song import windows, blank, append_window, finish
    from gameplay_options import seeded_generation
    _configure(bundle, config)
    _reseed(seed, config)
    log(f"Evaluation {arm}: loading explicit CPU production bundle")
    runtime = V2Runtime(bundle)
    reports, packages = [], []
    chart_indices = {r["song"].song_id: index for index, r in enumerate(references)}
    grouped = {}
    for reference in references:
        grouped.setdefault(reference["song"].metadata["group_id"], []).append(reference)
    try:
        for group_id, group in grouped.items():
            duration = float(group[0]["song"].metadata["duration"])
            plan = windows(duration)
            charts = {r["song"].song_id: blank() for r in group}
            parts = {r["song"].song_id: [] for r in group}
            for part, (start, end, core_start, core_end) in enumerate(plan):
                audio = group[0]["sources"]["audio"]
                if len(plan) > 1:
                    audio = output / f"context_{arm}_{group_id[:12]}_{part}.wav"
                    subprocess.run([str(_ffmpeg()), "-v", "error", "-nostdin", "-i",
                        str(group[0]["sources"]["audio"]), "-ss", str(start), "-t", str(end - start),
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(audio), "-y"],
                        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                # A prepared object is always made by this arm's runtime. In
                # particular, candidate intensity can never reuse baseline shares.
                prepared = runtime.prepare_audio(audio, float(group[0]["song"].metadata["bpm"]))
                for reference in group:
                    song = reference["song"]
                    nps = float(song.metadata["difficulty_nps"])
                    chart_seed = seed + chart_indices[song.song_id] + part * len(references)
                    log(f'Evaluation {arm}: {reference["difficulty"]}, NPS={nps:.4f}, seed={chart_seed}')
                    with scoped_settings():
                        _difficulty_settings(4 * nps)
                        _reseed(chart_seed, config)
                        with seeded_generation(chart_seed):
                            result = runtime.generate(prepared, nps, logger=lambda message: None)
                        chart, omissions = serialize_beatmap(result.state, 60., version="v3")
                        append_window(charts[song.song_id], chart, start, core_start, core_end)
                        parts[song.song_id].append({"part": part, "seed": chart_seed,
                            "source_bpm": prepared.song.metadata["bpm"], "requested_nps": nps,
                            "diagnostics": result.diagnostics, "omissions": omissions})
                        del result, chart
                if len(plan) > 1:
                    audio.unlink()
            for reference in group:
                song = reference["song"]
                chart = charts[song.song_id]
                stitching = finish(chart, duration, [p[2] for p in plan[1:]])
                if len(chart["colorNotes"]) < 4:
                    raise ValueError(f"{arm} {song.song_id}: fewer than four generated notes")
                reports.append({"song_id": song.song_id, "group_id": group_id,
                    "difficulty": reference["difficulty"], "song_name": song.metadata["song_name"],
                    "metrics": _chart_metrics(song, chart, float(song.metadata["difficulty_nps"])),
                    "parts": parts[song.song_id], "stitching": stitching})
            packages.append(_review_package(output / "review_packages" / f"{group_id[:12]}_{arm}", arm, group, charts))
    finally:
        del runtime
        tf.keras.backend.clear_session()
        gc.collect()
    return {"charts": reports, "review_packages": packages}


def _conditional(arm, bundle, beat_dataset, v2_dataset, log):
    import tensorflow as tf
    from tools.config import config
    from finetune_beats import evaluate as evaluate_beats
    from finetune_stages import evaluate as evaluate_stages
    _configure(bundle, config)
    document = _read(bundle / "bundle.json")
    result = {}
    for stage in STAGES:
        log(f"Evaluation {arm}: fixed-reference test loss for {stage}")
        path = bundle / document["model_stages"][stage]
        if stage in ("intensity", "timing"):
            result[stage] = evaluate_beats(stage, beat_dataset, path, split="test", batch_size=4, conditioning="reference")
        else:
            result[stage] = evaluate_stages(stage, v2_dataset, path, split="test")
        tf.keras.backend.clear_session()
        gc.collect()
    return result


def _summary(report, output):
    rows, text = [], ["Оценка дообучения InfernoSaber", "", "Только зафиксированный TEST; параметры по тесту не подбирались.",
        "Одинаковые аудио, BPM, запрошенный NPS, seed и настройки декодера для обеих моделей.", ""]
    for baseline, candidate in zip(report["baseline"]["charts"], report["candidate"]["charts"]):
        if baseline["song_id"] != candidate["song_id"]:
            raise ValueError("Comparison chart ordering mismatch")
        a, b = baseline["metrics"], candidate["metrics"]
        values = [baseline["difficulty"], f'{a["requested_nps"]:.3f}',
                  f'{a["generated_nps"]:.3f} → {b["generated_nps"]:.3f}',
                  f'{a["timing_20ms"]["f1"]:.4f} → {b["timing_20ms"]["f1"]:.4f}',
                  f'{a["timing_50ms"]["f1"]:.4f} → {b["timing_50ms"]["f1"]:.4f}',
                  f'{a["emitted_intensity_share_tv"]:.4f} → {b["emitted_intensity_share_tv"]:.4f}']
        rows.append("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in values) + "</tr>")
        text.append(" | ".join(values))
    conditional_rows = []
    text.extend(["", "Условная потеря на TEST (до → после; ниже лучше, внутри одной головы):"])
    for stage in STAGES:
        baseline = report["baseline"]["conditional_test_metrics"][stage]
        candidate = report["candidate"]["conditional_test_metrics"][stage]
        values = [stage, f'{baseline["loss"]:.6f}', f'{candidate["loss"]:.6f}']
        conditional_rows.append("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in values) + "</tr>")
        text.append(f'{stage}: {values[1]} → {values[2]}')
    limits = [
        f'Независимых тестовых записей: {report["split_audit"]["split_recordings"]["test"]}; несколько сложностей одного трека не являются независимыми песнями.',
        "Пересечения с исходным предобучением полностью неизвестны: проверены доступные ID и точные хеши, но они не доказывают независимость всех записей.",
        "Метрики совпадения с человеческой картой и структурные прокси не доказывают удобство или качество игры. Проверки в шлеме не было.",
        "Стены и бомбы отключены в сравнении; декоративные calm-эффекты отключены. Арки и стены в production используют исходные эвристики; улучшение потерь обученных голов само по себе не меняет эти эвристики.",
        "Условные потери голов используют фиксированный эталонный контекст; production-результат использует предсказания предыдущих этапов.",
        "Исходная нормализация, словари, аудиоэнкодер, освещение v1 и настройки плотности сохранены. Новые правила плотности не добавлялись.",
    ]
    report["limitations"] = limits
    text.extend(["", "Колонки: сложность | заданный NPS | фактический NPS до → после | F1 20мс | F1 50мс | TV профиля (меньше лучше)",
                 "", *limits, "", "Полные машинные метрики и условные потери: evaluation.json", ""])
    for arm in ("baseline", "candidate"):
        for package in report[arm]["review_packages"]:
            text.append(f'{arm}: {package["zip"]}')
    (output / "report_ru.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    document = '<!doctype html><meta charset="utf-8"><title>Оценка дообучения</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;line-height:1.5}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #bbb;text-align:left}th{background:#eef}</style>'
    document += '<h1>Дообучение: базовая модель → кандидат</h1><p>Зафиксированный TEST. Это измерения, а не вывод о качестве игры.</p><table><tr>'
    document += "".join(f"<th>{s}</th>" for s in ("Сложность", "Заданный NPS", "Фактический NPS", "Timing F1 20мс ↑", "Timing F1 50мс ↑", "TV профиля ↓")) + "</tr>" + "".join(rows) + "</table>"
    document += '<h2>Условная потеря на TEST</h2><p>Фиксированный эталонный контекст; ниже лучше внутри одной головы.</p><table><tr><th>Голова</th><th>До</th><th>После</th></tr>' + "".join(conditional_rows) + "</table>"
    document += "<ul>" + "".join(f"<li>{html.escape(s)}</li>" for s in limits) + "</ul>"
    for arm in ("baseline", "candidate"):
        for package in report[arm]["review_packages"]:
            path = Path(package["zip"]).relative_to(output).as_posix()
            document += f'<p><a href="{html.escape(path)}">Скачать {arm}: полный трек и карты v3</a></p>'
    document += '<p><a href="evaluation.json">Полные метрики и условные потери голов (JSON)</a></p>'
    (output / "report.html").write_text(document, encoding="utf-8")


def evaluate(base_bundle, candidate_bundle, beat_dataset, v2_dataset, output, seed=127, log=print):
    """Return a JSON-safe report and full-audio native-v3 A/B review packages."""
    started = time.perf_counter()
    base_bundle, candidate_bundle, beat_dataset, v2_dataset, output = (
        Path(p).resolve() for p in (base_bundle, candidate_bundle, beat_dataset, v2_dataset, output))
    if (output / "evaluation.json").exists() or (output / "review_packages").exists():
        raise ValueError("Use a fresh evaluation output directory; previous evidence is preserved")
    if type(seed) is not int or seed < 0:
        raise ValueError("Evaluation seed must be a nonnegative integer")
    output.mkdir(parents=True, exist_ok=True)
    with _environment():
        from tools.config import config
        from map_creation.v2_generation import scoped_settings, configure_inference, settings_snapshot
        from map_creation.v2_runtime import validate_production_bundle
        from training.beat_models.artifacts import load_dataset
        from training.pipeline_v2.artifacts import load_dataset as load_v2
        with scoped_settings():
            _configure(base_bundle, config)
            configure_inference()
            songs, beat_manifest = load_dataset(beat_dataset)
            v2 = load_v2(v2_dataset)
            audit = _split_audit(beat_manifest, v2.manifest)
            tests = [song for song in songs if song.metadata["split"] == "test"]
            references = []
            for song in tests:
                sources, info = _verified_sources(song.metadata)
                references.append({"song": song, "sources": sources, "info": info,
                    "difficulty": song.metadata["provenance"]["difficulty"]})
            tests_by_id = {song.song_id: song for song in tests}
            for index, record in enumerate(v2.manifest["songs"]):
                if record["split"] == "test":
                    song = v2.songs[index]
                    if song.base.song_id not in tests_by_id:
                        raise ValueError("V2 test chart missing from beat dataset")
            del songs, v2
            validated = {}
            for arm, bundle in (("baseline", base_bundle), ("candidate", candidate_bundle)):
                _configure(bundle, config)
                validated[arm] = validate_production_bundle(bundle)
            a, b = validated["baseline"], validated["candidate"]
            for key in ("normalization", "vocabularies", "encoder_identity"):
                if a[key] != b[key]:
                    raise ValueError(f"A/B bundles changed the shared {key} contract")
            if a["bundle"]["data_settings"] != b["bundle"]["data_settings"]:
                raise ValueError("A/B bundles must use identical decoder/data settings")
            for filename in ("tf_event_gen_2_12_5_5__22_18.h5", "onehot_encoder_events.pkl"):
                if _hash(base_bundle / "v1_lighting" / filename) != _hash(candidate_bundle / "v1_lighting" / filename):
                    raise ValueError("A/B lighting assets differ")
            fine_audit = _candidate_audit(a["manifests"], b["manifests"], beat_manifest)
            test_hashes = {source["sha256"] for song in tests for source in song.metadata["sources"] if source["role"] == "audio"}
            overlap = {stage: sorted(test_hashes.intersection(source.get("sha256") for source in manifest.get("sources", []) if source.get("role") == "audio"))
                       for stage, manifest in a["manifests"].items()}
            chart_overlap = {stage: {split: sorted(set(audit["test_song_ids"]).intersection(ids))
                                    for split, ids in manifest.get("splits", {}).items()}
                             for stage, manifest in a["manifests"].items()}
            snapshot = settings_snapshot()["config"]
            decoder = {key: value for key, value in snapshot.items()
                       if key.startswith("v2_") or key in ("add_bombs_flag", "add_obstacle_flag", "add_slider_flag", "min_time_diff")}
            report = {"kind": "frozen_heldout_production_ab_evaluation", "schema_version": 1,
                "rng_control": "explicit per-variant NumPy Generator; scoped Python and legacy NumPy RNG states",
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "device": "CPU", "seed": seed,
                "evaluation_split": "test", "test_used_for_fitting_or_selection": False,
                "split_audit": audit, "candidate_fine_tuning_audit": fine_audit,
                "base_pretraining_exact_audio_hash_matches": overlap,
                "base_pretraining_chart_id_matches": chart_overlap,
                "base_pretraining_recording_independence": "unknown", "decoder_settings": decoder,
                "bundle_calibrations": {arm: value["calibrations"] for arm, value in validated.items()},
                "difficulty_settings": "unchanged _difficulty_settings(4 * requested_nps)",
                "reference_nps_policy": "prepared retained colored notes / full audio duration, identical for both arms",
                "metric_definitions": {"timing": "one-to-one synchronized retained-reference onset matching at 20/50 ms",
                    "matched_hands": "reference-like placement/direction only at matched 20-ms onsets; not gameplay quality",
                    "intensity_tv": "half L1 distance between emitted one-second note shares and retained reference shares",
                    "structural": "counts only; repeated cut direction is a proxy, not a parity/playability test"},
                "datasets": {"beat": str(beat_dataset), "v2": str(v2_dataset),
                    "beat_manifest_sha256": _hash(beat_dataset / "manifest.json"),
                    "v2_manifest_sha256": _hash(v2_dataset / "manifest.json")},
                "bundles": {arm: {"path": str(value["root"]), "bundle_sha256": _hash(value["root"] / "bundle.json"),
                    "checkpoint_sha256": {stage: manifest["checkpoint_sha256"] for stage, manifest in value["manifests"].items()}}
                    for arm, value in validated.items()},
                "sources": [{"song_id": song.song_id, "metadata": {key: song.metadata[key] for key in
                    ("group_id", "audio_sha256", "decoded_sha256", "sources", "provenance", "duration", "bpm", "difficulty_nps")}} for song in tests]}
            for arm, bundle in (("baseline", base_bundle), ("candidate", candidate_bundle)):
                report[arm] = _generate_arm(arm, bundle, references, output, seed, log)
            for arm, bundle in (("baseline", base_bundle), ("candidate", candidate_bundle)):
                report[arm]["conditional_test_metrics"] = _conditional(arm, bundle, beat_dataset, v2_dataset, log)
            report["elapsed_minutes"] = (time.perf_counter() - started) / 60
            report["status"] = "evaluated"
            _summary(report, output)
            _write(output / "evaluation.json", report)
            log(f"Held-out comparison complete: {output / 'report.html'}")
            # Round-trip also guarantees the public return value is JSON-safe.
            return _read(output / "evaluation.json")
