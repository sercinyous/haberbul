import yaml

from gundem.seeds import SEEDS_DIR

VOICE_FILE = SEEDS_DIR / "voice_examples.yaml"


def load_voice_examples() -> list[dict]:
    """Editor-approved Pusholder posts used as style inspiration. Missing file means no examples."""
    if not VOICE_FILE.exists():
        return []
    rows = yaml.safe_load(VOICE_FILE.read_text(encoding="utf-8")) or []
    return [row for row in rows if isinstance(row, dict) and row.get("text")]
