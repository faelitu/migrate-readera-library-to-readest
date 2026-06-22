"""
TODO: write docstring
"""

import re
import unicodedata

def normalize_title(title: str) -> str:
    """
    Normalizes a title for comparison, tolerant of spelling variations:
    converts to lowercase, removes accents and extra whitespace.

    Example: "Crepúsculo dos Ídolos" → "crepusculo dos idolos"
    """
    if not title:
        return ''

    # remove diacritics
    nfd = unicodedata.normalize('NFD', title)
    sem_acentos = ''.join(c for c in nfd if not unicodedata.combining(c))
    s = sem_acentos.lower()

    # remove common noisy prefixes
    prefixes = [r'^microsoft word -', r'^ebook\s*-?', r'^pdfcoffee\.com_', r'^pdfcoffee\.com', r'^dokumen\.pub_', r'^ebook\s']
    for p in prefixes:
        s = re.sub(p, ' ', s)

    # normalize separators
    s = s.replace('_', ' ').replace('-', ' ')

    # remove line breaks
    s = s.replace('\n', ' ')

    # strip common file extensions
    s = re.sub(r"\.(rtf|docx?|pdf|epub)$", '', s)

    # remove non-alphanumeric (keep spaces)
    s = re.sub(r"[^0-9a-z ]+", ' ', s)

    # collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    return s
