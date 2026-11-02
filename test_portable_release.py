"""Publication-sensitive validation of native audio and first-run game resources."""
import hashlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT.parent / 'beat_saber_lab/python_packages'), str(ROOT)]
from engine import bank
from native_audio import raw_to_wav
from portable_assets import game_assets, ensure_ffmpeg


class PortableReleaseTest(unittest.TestCase):
    def test_pcm_samples_and_length_survive_native_bank_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test_work') as temp:
            path = Path(temp) / 'sample.wav'
            pcm = struct.pack('<hhhhhh', -32768, 32767, -20, 17, 0, 1)
            with wave.open(str(path), 'wb') as audio:
                audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(44100); audio.writeframes(pcm)
            raw, frames, digest = bank(path)
            decoded = next(iter(raw_to_wav(raw, 'sample', 2, 44100).values()))
            with wave.open(io.BytesIO(decoded)) as audio:
                self.assertEqual(audio.getnframes(), frames)
                self.assertEqual(hashlib.sha256(audio.readframes(frames)).hexdigest(), digest)

    def test_truncated_native_bank_is_rejected(self):
        raw = struct.pack('<4s6I8s16s8s', b'FSB5', 1, 1, 36, 0, 32, 2, b'\0'*8, b'\0'*16, b'\0'*8)
        raw += struct.pack('<Q', (100 << 34) | (1 << 5) | (8 << 1)) + b'\0' * 60
        with self.assertRaises(ValueError):
            raw_to_wav(raw, 'broken', 2, 44100)

    def test_existing_ffmpeg_does_not_request_network(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test_work') as temp:
            assets = Path(temp) / 'assets'; (assets / 'tools').mkdir(parents=True)
            expected = assets / 'tools/ffmpeg.exe'; expected.write_bytes(b'existing')
            with patch('portable_assets._open', side_effect=AssertionError('network forbidden')):
                self.assertEqual(ensure_ffmpeg(assets, Path(temp) / 'cache'), expected)

    def test_missing_espresso_cannot_silently_use_another_users_resource(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test_work') as temp:
            with self.assertRaisesRegex(Exception, 'espresso'):
                game_assets(Path(temp) / 'source.obb', Path(temp) / 'assets', Path(temp) / 'cache')


if __name__ == '__main__':
    unittest.main()
