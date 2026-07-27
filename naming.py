import re

UNSAFE_CHARS = '"?*<>|/'


def clean_title(title):
    text = title.replace(":", " -")
    text = text.replace("'", "").replace("’", "")
    for ch in UNSAFE_CHARS:
        text = text.replace(ch, "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[\s-]+$", "", text).strip()
    return text
