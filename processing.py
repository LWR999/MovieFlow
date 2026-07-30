import re
import subprocess
from pathlib import Path

import db
import probe

MKVMERGE_TIMEOUT = 6 * 60 * 60

# TMDb original_language is ISO 639-1 (2-letter); mkvmerge reports track
# languages as ISO 639-2 (3-letter), which has both a bibliographic and
# terminological code for some languages (e.g. French: fre/fra) - accept
# either.
ISO_639_1_TO_639_2 = {
    "en": {"eng"},
    "fr": {"fre", "fra"},
    "de": {"ger", "deu"},
    "ja": {"jpn"},
    "ru": {"rus"},
    "es": {"spa"},
    "it": {"ita"},
    "pt": {"por"},
    "ko": {"kor"},
    "zh": {"chi", "zho"},
    "nl": {"dut", "nld"},
    "sv": {"swe"},
    "no": {"nor"},
    "da": {"dan"},
    "fi": {"fin"},
    "pl": {"pol"},
    "tr": {"tur"},
    "ar": {"ara"},
    "hi": {"hin"},
}


def _keep_audio_track_ids(tracks, is_foreign, original_language):
    """Per spec section 1.2: keep every English-tagged audio track, plus (for
    foreign films) the original-language track(s). Drop everything else."""
    english_codes = ISO_639_1_TO_639_2["en"]
    original_codes = ISO_639_1_TO_639_2.get(original_language or "", set())

    keep_ids = []
    for t in tracks:
        if t.get("type") != "audio":
            continue
        lang = (t.get("properties", {}).get("language") or "").lower()
        if lang in english_codes:
            keep_ids.append(t["id"])
        elif is_foreign and lang in original_codes:
            keep_ids.append(t["id"])
    return keep_ids


def _run_mkvmerge_strip(input_path, output_path, keep_ids, progress):
    cmd = [
        "mkvmerge", "--gui-mode",
        "-o", str(output_path),
        "--audio-tracks", ",".join(str(i) for i in keep_ids),
        str(input_path),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pattern = re.compile(r"#GUI#progress (\d+)%")
    for line in proc.stdout:
        m = pattern.search(line)
        if m:
            pct = 10 + min(80, int(m.group(1)) * 0.8)
            progress(pct, "stripping audio tracks")
    proc.wait(timeout=MKVMERGE_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"mkvmerge exited with code {proc.returncode}")


def process_audio(movie_id, progress):
    conn = db.connect()
    try:
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            raise RuntimeError(f"movie {movie_id} not found")

        mkv_path = Path(movie["final_path"]) / f"{movie['clean_title']} ({movie['year']}).mkv"
        if not mkv_path.is_file():
            raise RuntimeError(f"expected MKV not found: {mkv_path}")

        progress(5, "enumerating audio tracks")
        tracks = probe.mkvmerge_tracks(mkv_path)
        if not tracks:
            raise RuntimeError("mkvmerge could not identify tracks")

        keep_ids = _keep_audio_track_ids(tracks, movie["is_foreign"], movie["original_language"])
        if not keep_ids:
            raise RuntimeError("no audio tracks matched the retention rule - refusing to strip all audio")

        all_audio_ids = [t["id"] for t in tracks if t.get("type") == "audio"]
        if set(keep_ids) == set(all_audio_ids):
            # Every audio track already matches the retention rule - a
            # remux here would just read and rewrite the whole file for no
            # benefit.
            progress(100, "no audio tracks to strip")
            conn.execute(
                "UPDATE movies SET status = 'processed', updated_at = datetime('now') WHERE id = ?",
                (movie_id,),
            )
            conn.commit()
            return

        progress(10, "stripping audio tracks")
        tmp_output = mkv_path.with_suffix(".tmp.mkv")
        _run_mkvmerge_strip(mkv_path, tmp_output, keep_ids, progress)

        progress(90, "verifying output")
        if not tmp_output.exists() or tmp_output.stat().st_size == 0:
            raise RuntimeError("mkvmerge output missing or empty")

        progress(95, "replacing original file")
        tmp_output.replace(mkv_path)

        progress(97, "refreshing media info")
        inspection = probe.inspect_media(str(mkv_path), "mkv")

        conn.execute(
            """
            UPDATE movies SET status = 'processed',
                resolution = COALESCE(?, resolution),
                hdr_flavor = COALESCE(?, hdr_flavor),
                audio_summary = COALESCE(?, audio_summary),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (inspection["resolution"], inspection["hdr_flavor"], inspection["audio_summary"], movie_id),
        )
        conn.commit()
        progress(100, "done")
    finally:
        conn.close()
