"""Text cleanup used for matching. No network, no state, easy to test."""
import re
from difflib import SequenceMatcher

# Long and short forms that mean the same thing in a street address.
_STREET_WORDS = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr",
    "lane": "ln", "boulevard": "blvd", "court": "ct", "place": "pl",
    "pk": "pike", "highway": "hwy", "parkway": "pkwy", "circle": "cir",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
}

# Spelling variants in facility names.
_NAME_WORDS = {"rehab": "rehabilitation", "centre": "center", "ctr": "center"}
_NAME_STOPWORDS = {"the", "of", "at"}


def _tokens(text):
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()


def norm_street(street):
    """'4850 Northwest Sylvania Avenue' and '4850 NW Sylvania Ave' both become '4850 nw sylvania ave'."""
    return " ".join(_STREET_WORDS.get(t, t) for t in _tokens(street))


def is_po_box(street):
    return bool(re.match(r"^\s*p\.?\s*o\.?\s*box\b", (street or "").lower()))


def norm_name(name):
    """Lowercase, drop punctuation and filler words, unify spelling variants."""
    text = (name or "").lower().replace("&", " and ")
    text = " ".join(_tokens(text)).replace("health care", "healthcare")
    words = [_NAME_WORDS.get(t, t) for t in text.split()]
    return " ".join(w for w in words if w not in _NAME_STOPWORDS)


def name_similarity(a, b):
    """0 to 1. 1.0 means the names are the same after cleanup."""
    return SequenceMatcher(None, norm_name(a), norm_name(b)).ratio()


def norm_phone(phone):
    return re.sub(r"\D", "", phone or "")


def norm_zip(zip_code):
    return re.sub(r"\D", "", zip_code or "")[:5]


def norm_text(text):
    """For city and person names: case and spacing do not matter."""
    return " ".join(_tokens(text))
