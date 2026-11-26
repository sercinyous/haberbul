import re

from rapidfuzz import fuzz

from gundem.topics import normalize_title

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[^\W\d_][\w'’]*")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_APOSTROPHE = re.compile(r"['’]")
_SENTENCE = re.compile(r"[.!?\n]+")
_LETTERS = re.compile(r"[^\W\d_]+")
MIN_SENTENCE = 30
# Same list as the prompt's "YASAK KALIPLAR", Turkish case-folded.
BANNED_PHRASES = ("şok", "inanamayacaksınız", "dikkat çekti", "gündem oldu", "işte detaylar")
_BANNED = [(p, re.compile(rf"(?<!\w){re.escape(p)}(?!\w)")) for p in BANNED_PHRASES]
CAPS_FLAG = "yasak kalıp: tamamı büyük harf"


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


def max_sentence_similarity(texts: list[str], source_text: str) -> float:
    """Highest source_similarity of any sentence (>= 30 chars) of any text.

    Scoring sentences separately catches a copied source sentence padded with original text.
    A text with no long-enough sentence is scored whole. Texts shorter than 30 chars are skipped:
    partial_ratio scores any short fragment found in the source (e.g. a slide "12 durak") at ~1.0.
    """
    best = 0.0
    for text in texts:
        if len(text.strip()) < MIN_SENTENCE:
            continue
        sentences = [s.strip() for s in _SENTENCE.split(text) if len(s.strip()) >= MIN_SENTENCE] or [text]
        best = max([best] + [source_similarity(s, source_text) for s in sentences])
    return best


def style_flags(text: str) -> list[str]:
    """Banned phrases (case-folded) and runs of >= 3 consecutive ALL-CAPS words of length >= 3."""
    folded = _fold(text)
    flags = [f"yasak kalıp: {phrase}" for phrase, pattern in _BANNED if pattern.search(folded)]
    run = 0
    for word in _LETTERS.findall(text):
        run = run + 1 if len(word) >= 3 and word.isupper() else 0
        if run >= 3:
            flags.append(CAPS_FLAG)
            break
    return flags


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _number_key(number: str) -> str:
    return number.replace(",", ".")


def _normalize(text: str) -> str:
    # Apostrophes become spaces so "Fenerbahçe'yi" still yields the word "fenerbahçe".
    return normalize_title(_APOSTROPHE.sub(" ", text))
