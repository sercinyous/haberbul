from functools import lru_cache

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from gundem.models import BanKeyword, Item, Topic
from gundem.seeds import SEEDS_DIR
from gundem.topics import normalize_title

_RETRACTION_TERMS = ("yalanladı", "düzeltme", "correction", "retracted", "asılsız")


@lru_cache
def _sensitive_phrases() -> tuple[str, ...]:
    path = SEEDS_DIR / "sensitive_keywords.yaml"
    if not path.exists():
        return ()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    phrases = (normalize_title(str(k)) for k in data.get("keywords", []))
    return tuple(dict.fromkeys(p for p in phrases if p))


def _haystack(item: Item) -> str:
    return normalize_title(f"{item.title} {item.summary or ''}")


def _matches(text: str, phrases) -> bool:
    padded = f" {text} "
    return any(f" {p} " in padded for p in phrases)


def apply_safety(topic: Topic, items: list[Item], session: Session) -> None:
    """Flag a topic as banned/sensitive from its items. Only ever sets flags to True."""
    text = " ".join(_haystack(i) for i in items)
    bans = [
        p for phrase in session.scalars(select(BanKeyword.phrase).where(BanKeyword.active.is_(True)))
        if (p := normalize_title(phrase))
    ]
    if _matches(text, bans):
        topic.banned = True
    if _matches(text, _sensitive_phrases()):
        topic.sensitive = True
    session.flush()


def detect_retraction(item: Item) -> bool:
    text = _haystack(item)
    return any(term in text for term in _RETRACTION_TERMS)
