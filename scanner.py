import os
import re
from pathlib import Path

import config

VIDEO_EXTENSIONS = {".mkv", ".mp4"}

QUALITY_KEYWORDS = [
    "2160p", "1080p", "720p", "480p",
    "WEBRip", "WEB-DL", "WEBDL", "WEB",
    "BluRay", "Blu-Ray",
    "HDTV", "REMUX",
    "x264", "x265", "h264", "h265", "HEVC", "AVC",
    "AAC", "DTS", "TrueHD", "Atmos", "DDP", "DD5.1",
]
QUALITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in sorted(QUALITY_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def parse_title_year(raw_name):
    name = raw_name.replace(".", " ").replace("_", " ")

    quality_match = QUALITY_RE.search(name)
    if quality_match:
        name = name[:quality_match.start()]

    year = None
    year_matches = list(YEAR_RE.finditer(name))
    if year_matches:
        last = year_matches[-1]
        year = int(last.group(1))
        name = name[:last.start()] + name[last.end():]

    title = re.sub(r"\s+", " ", name).strip(" -")
    return title, year


def _find_first_video_suffix(folder: Path):
    for _, _, filenames in os.walk(folder):
        for fname in sorted(filenames):
            suffix = Path(fname).suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                return suffix
    return None


def discover_items(root=None, folders=None):
    root = Path(root if root is not None else config.TORRENTS_ROOT)
    folders = folders if folders is not None else config.INTAKE_FOLDERS

    items = []
    for folder_name in folders:
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if entry.is_file():
                suffix = entry.suffix.lower()
                if suffix in VIDEO_EXTENSIONS:
                    items.append({
                        "original_path": str(entry),
                        "source_type": suffix.lstrip("."),
                        "raw_name": entry.stem,
                    })
            elif entry.is_dir():
                if (entry / "BDMV").is_dir():
                    items.append({
                        "original_path": str(entry),
                        "source_type": "bdmv",
                        "raw_name": entry.name,
                    })
                    continue
                suffix = _find_first_video_suffix(entry)
                if suffix:
                    items.append({
                        "original_path": str(entry),
                        "source_type": suffix.lstrip("."),
                        "raw_name": entry.name,
                    })
    return items


def sync_discovered(db):
    existing = {row["original_path"] for row in db.execute("SELECT original_path FROM movies")}
    inserted = 0
    for item in discover_items():
        if item["original_path"] in existing:
            continue
        title, year = parse_title_year(item["raw_name"])
        db.execute(
            "INSERT INTO movies (original_path, source_type, title, year) VALUES (?, ?, ?, ?)",
            (item["original_path"], item["source_type"], title, year),
        )
        inserted += 1
    if inserted:
        db.commit()
    return inserted
