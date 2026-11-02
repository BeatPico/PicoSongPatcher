"""Decode the patcher's single-sample PCM16 FSB5 banks without FMOD binaries."""
import io
import struct
import sys
import types
import wave


def raw_to_wav(raw_data, name, channels, frequency, convert_pcm_float=True):
    raw = bytes(raw_data)
    if len(raw) < 68 or raw[:4] != b'FSB5':
        raise ValueError('Не поддерживается формат встроенного аудио. Используй исходный MP3/ZIP для пересоздания AI.')
    version, count, headers, names, size, mode = struct.unpack_from('<6I', raw, 4)
    sample = struct.unpack_from('<Q', raw, 60)[0]
    if version != 1 or count != 1 or mode != 2 or headers < 8 or sample & 1:
        raise ValueError('Для пересоздания этого сжатого игрового аудио нужен исходный MP3/ZIP. Готовую карту можно переиспользовать без декодирования.')
    frames = sample >> 34
    channel_count = 2 if (sample >> 5) & 1 else 1
    frequencies = (0, 8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000)
    rate_index = (sample >> 1) & 15
    if rate_index >= len(frequencies) or not frequencies[rate_index]:
        raise ValueError('Неизвестная частота PCM-банка.')
    rate = frequencies[rate_index]
    offset = 60 + headers + names
    sample_offset = ((sample >> 7) & ((1 << 27) - 1)) * 32
    length = frames * channel_count * 2
    if channels != channel_count or frequency != rate or sample_offset + length > size or offset + size > len(raw):
        raise ValueError('Повреждены параметры PCM-банка.')
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(channel_count)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(raw[offset + sample_offset:offset + sample_offset + length])
    return {str(name) + '.wav': output.getvalue()}


def install():
    module = types.ModuleType('fmod_toolkit')
    module.raw_to_wav = raw_to_wav
    sys.modules['fmod_toolkit'] = module
