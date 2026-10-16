import re
import unicodedata

MAX_BASE_LENGTH = 60
FALLBACK_BASE = "evento"


def _ascii_lower(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return folded.lower()


def make_slug(title: str, importer_id: str) -> str:
    """Build a readable, stable slug: ``<title-words>-<6-char id suffix>``.

    The slug is assigned once at first publish and never recomputed, so renaming an
    event upstream does not break links that were already shared.
    """
    base = re.sub(r"[^a-z0-9]+", "-", _ascii_lower(title)).strip("-")
    if len(base) > MAX_BASE_LENGTH:
        base = base[:MAX_BASE_LENGTH].rsplit("-", 1)[0]
    base = base or FALLBACK_BASE
    suffix = re.sub(r"[^a-z0-9]", "", importer_id.lower())[:6] or "x"
    return f"{base}-{suffix}"
