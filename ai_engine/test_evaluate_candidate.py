"""Small evidence/serialization checks; these do not run trained models."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

ENGINE = Path(__file__).resolve().parent
sys.path[:0] = [str(ENGINE), str(ENGINE / "infernosaber")]
import evaluate_candidate as evaluation


def metadata(split, number):
    return {"split": split, "group_id": "group" + str(number),
            "audio_sha256": "audio" + str(number), "decoded_sha256": "decoded" + str(number)}


class EvaluationEvidenceTests(unittest.TestCase):
    def fixture(self):
        records = [{"song_id": name, "metadata": metadata(split, number)} for number, (name, split) in
                   enumerate((("training", "train"), ("validation", "validation"), ("heldout", "test")))]
        beat = {"songs": records, "splits": {split: [r["song_id"] for r in records if r["metadata"]["split"] == split]
                                            for split in ("train", "validation", "test")}}
        v2 = {"songs": [{"song_id": r["song_id"], **r["metadata"]} for r in records]}
        return beat, v2

    def test_cross_encoding_identity_cannot_cross_splits(self):
        beat, v2 = self.fixture()
        self.assertEqual(evaluation._split_audit(beat, v2)["split_recordings"]["test"], 1)
        beat["songs"][2]["metadata"]["decoded_sha256"] = beat["songs"][0]["metadata"]["decoded_sha256"]
        with self.assertRaisesRegex(ValueError, "crosses held-out"):
            evaluation._split_audit(beat, v2)

    def test_new_weights_require_disjoint_optimizer_provenance(self):
        beat, _ = self.fixture()
        baseline = {stage: {"checkpoint_sha256": "original"} for stage in evaluation.STAGES}
        fine = {"train_song_ids": ["training"], "validation_song_ids": ["validation"],
                "training_recordings": [{"song_id": "training", **beat["songs"][0]["metadata"]}]}
        candidate = {stage: {"checkpoint_sha256": "candidate", "training": {"fine_tuning": deepcopy(fine)}}
                     for stage in evaluation.STAGES}
        audit = evaluation._candidate_audit(baseline, candidate, beat)
        self.assertTrue(audit["timing"]["checkpoint_changed"])
        candidate["timing"]["training"]["fine_tuning"]["validation_song_ids"] = ["heldout"]
        with self.assertRaisesRegex(ValueError, "checkpoint selection"):
            evaluation._candidate_audit(baseline, candidate, beat)

    def test_source_provenance_rejects_changed_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {"info": {"_version": "2.1.0", "_beatsPerMinute": 95}, "beatmap": {}, "audio": {}}
            sources = []
            for role, document in files.items():
                path = root / (role + ".dat")
                path.write_text(json.dumps(document), encoding="utf-8")
                sources.append({"role": role, "path": str(path), "sha256": evaluation._hash(path)})
            meta = {"bpm": 95, "sources": sources, "provenance": {"metadata_verified": True, "characteristic": "Standard"}}
            evaluation._verified_sources(meta)
            (root / "audio.dat").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Original source changed"):
                evaluation._verified_sources(meta)

    def test_perfect_generated_reference_metrics_and_native_audio_package(self):
        import numpy as np
        from full_song import blank
        from training.beat_models.contracts import encode_note
        chart = blank()
        chart["colorNotes"] = [{"b": 2., "x": 1, "y": 0, "c": 0, "d": 1, "a": 0},
                               {"b": 3., "x": 2, "y": 0, "c": 1, "d": 0, "a": 0}]
        song = SimpleNamespace(song_id="test", metadata={"duration": 5.}, arrays={
            "event_times": np.array([2., 3.]), "note_classes": np.array([[encode_note(1, 0, 1), 0], [0, encode_note(2, 0, 0)]]),
            "original_notes": np.zeros((2, 8)), "bin_durations": np.ones(5),
            "intensity": np.array([0, 0, .5, .5, 0])})
        metrics = evaluation._chart_metrics(song, chart, .4)
        self.assertEqual(metrics["timing_20ms"]["f1"], 1.)
        self.assertEqual(metrics["emitted_intensity_share_tv"], 0.)
        self.assertEqual(metrics["hands_at_matched_20ms_onsets"]["red"]["direction_accuracy"], 1.)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio, cover, source_info = root / "song.egg", root / "cover.png", root / "Info.dat"
            audio.write_bytes(b"entire original audio"); cover.write_bytes(b"original cover")
            info = {"_version": "2.1.0", "_songName": "Source", "_levelAuthorName": "Mapper", "_beatsPerMinute": 95.,
                    "_songFilename": "song.egg", "_coverImageFilename": "cover.png", "_difficultyBeatmapSets": [
                        {"_beatmapCharacteristicName": "Standard", "_difficultyBeatmaps": [
                            {"_difficulty": "Hard", "_difficultyRank": 5, "_beatmapFilename": "old.dat", "_noteJumpMovementSpeed": 13}]}]}
            reference = {"song": song, "difficulty": "Hard", "info": info, "sources": {"audio": audio, "info": source_info}}
            package = evaluation._review_package(root / "baseline", "baseline", [reference], {"test": chart})
            self.assertEqual(evaluation._hash(audio), package["audio_sha256"])
            saved = evaluation._read(root / "baseline" / "HardStandard.dat")
            self.assertAlmostEqual(saved["colorNotes"][0]["b"] * 60 / 95, 2.)
            self.assertEqual(chart["colorNotes"][0]["b"], 2.)
            self.assertEqual(evaluation._read(root / "baseline" / "Info.dat")["_songName"], "Source [Pico BASELINE]")
            with zipfile.ZipFile(package["zip"]) as archive:
                self.assertEqual(archive.read("song.egg"), audio.read_bytes())


if __name__ == "__main__":
    unittest.main()
