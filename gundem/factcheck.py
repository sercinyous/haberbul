import re

from rapidfuzz import fuzz

from gundem.topics import normalize_title

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[^\W\d_][\w'’]*")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_APOSTROPHE = re.compile(r"['’]")


def factcheck(text: str, source_text: str, credit: str = "") -> list[str]:
    """Numbers and mid-sentence capitalized words in `text` that the source text does not contain.

    Heuristic only: a flag means "an editor should check this", not "this is wrong".
    """
    if credit and text.rstrip().endswith(credit):
        text = text.rstrip()[: -len(credit)]
    source_numbers = {_number_key(m.group()) for m in _NUMBER.finditer(source_text)}
    source_words = f" {_normalize(source_text)}"

    found: list[tuple[int, str]] = []
    for m in _NUMBER.finditer(text):
        if _number_key(m.group()) not in source_numbers:
            found.append((m.start(), m.group()))
    offset = 0
    for sentence in _SENTENCE_END.split(text):
        start = text.find(sentence, offset)
        offset = start + len(sentence)
        for index, m in enumerate(_WORD.finditer(sentence)):
            stem = _APOSTROPHE.split(m.group())[0]
            if index == 0 or len(stem) <= 2 or not stem[0].isupper():
                continue
            key = _normalize(stem)
            if key and f" {key}" not in source_words:
                found.append((start + m.start(), stem))

    flags: list[str] = []
    for _, flag in sorted(found):
        if flag not in flags:
            flags.append(flag)
    return flags


def source_similarity(text: str, source_text: str) -> float:
    return fuzz.partial_ratio(text, source_text) / 100


def _number_key(number: str) -> str:
    return number.replace(",", ".")


def _normalize(text: str) -> str:
    # Apostrophes become spaces so "Fenerbahçe'yi" still yields the word "fenerbahçe".
    return normalize_title(_APOSTROPHE.sub(" ", text))
