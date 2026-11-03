"""Normalize display metadata while preserving the song's original script."""
import re
import unicodedata
from pathlib import Path


def clean_song_name(value, source_name=''):
    reference = unicodedata.normalize('NFC', Path(source_name).stem).casefold()
    # Some ID3 tags were interpreted with the wrong UTF-16 byte order. Only
    # recover a Cyrillic candidate if its beginning agrees with the filename.
    try:
        recovered = value.encode('utf-16-le').decode('utf-16-be').lstrip('\ufeff')
    except UnicodeError:
        recovered = ''
    words = re.findall(r'[А-Яа-яЁё]+', recovered)
    if len(words) >= 1 and not re.search(r'[А-Яа-яЁё]', value):
        prefix = ' '.join(words[:2]).casefold()
        if len(prefix) >= 3 and re.search(r'(?<!\w)' + re.escape(prefix) + r'(?!\w)', reference):
            value = recovered
    value = ''.join(' ' if unicodedata.category(c) in ('Cc', 'Cs') else c for c in value if c != '\ufeff')
    value = re.sub(r'\s*[\[(]hitmos\.fm[\])]\s*$', '', value, flags=re.IGNORECASE)
    return unicodedata.normalize('NFC', ' '.join(value.split()))
