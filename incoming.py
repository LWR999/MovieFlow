import shutil
from pathlib import Path

import config
import naming
import preprocess
import probe
import scanner
import tmdb


def discover_incoming_items(root=None):
    root = Path(root if root is not None else config.TORRENTS_ROOT_INCOMING)
    if not root.is_dir():
        return []
    return scanner.scan_folder_entries(root, skip_names={"TV"})


def _auto_match(title, year, imdb_id=None):
    """Best-effort auto-match. The release's own IMDb id (if the folder name
    carried one) is authoritative - use it directly rather than risk a wrong
    fuzzy match. Otherwise fall back to TMDb's top title/year search result.
    Returns a dict of match fields to apply, or None if nothing matched or
    the lookup failed - the item just falls back to the manual needs-match
    flow."""
    if imdb_id:
        try:
            found = tmdb.find_by_imdb_id(imdb_id)
        except tmdb.TMDbError:
            found = None
        if found:
            is_foreign = 1 if found["original_language"] and found["original_language"] != "en" else 0
            return {
                "tmdb_id": found["tmdb_id"],
                "imdb_id": imdb_id,
                "title": found["title"],
                "year": found["year"],
                "clean_title": naming.clean_title(found["title"]),
                "original_language": found["original_language"],
                "poster_path": found["poster_path"],
                "is_foreign": is_foreign,
            }

    if not title or not title.strip():
        return None
    try:
        candidates = tmdb.search_movies(title, year)
    except tmdb.TMDbError:
        return None
    if not candidates:
        return None

    top = candidates[0]
    try:
        external_ids = tmdb.get_external_ids(top["tmdb_id"])
    except tmdb.TMDbError:
        return None

    is_foreign = 1 if top["original_language"] and top["original_language"] != "en" else 0
    return {
        "tmdb_id": top["tmdb_id"],
        "imdb_id": external_ids["imdb_id"],
        "title": top["title"],
        "year": top["year"],
        "clean_title": naming.clean_title(top["title"]),
        "original_language": top["original_language"],
        "poster_path": top["poster_path"],
        "is_foreign": is_foreign,
    }


def sync_incoming(conn):
    existing = {row["original_path"] for row in conn.execute("SELECT original_path FROM movies")}
    inserted = 0
    for item in discover_incoming_items():
        if item["original_path"] in existing:
            continue
        title, year, imdb_id = scanner.parse_title_year(item["raw_name"])
        inspection = probe.inspect_media(item["original_path"], item["source_type"])

        cur = conn.execute(
            """
            INSERT INTO movies (original_path, source_type, title, year, resolution, hdr_flavor,
                audio_summary, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'incoming')
            """,
            (
                item["original_path"], item["source_type"], title, year,
                inspection["resolution"], inspection["hdr_flavor"], inspection["audio_summary"],
            ),
        )
        movie_id = cur.lastrowid

        match = _auto_match(title, year, imdb_id)
        if match:
            conn.execute(
                """
                UPDATE movies
                SET tmdb_id = ?, imdb_id = ?, title = ?, year = ?, clean_title = ?,
                    original_language = ?, poster_path = ?, is_foreign = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    match["tmdb_id"], match["imdb_id"], match["title"], match["year"],
                    match["clean_title"], match["original_language"], match["poster_path"],
                    match["is_foreign"], movie_id,
                ),
            )

        inserted += 1
    if inserted:
        conn.commit()
    return inserted


def category_for(source_type, is_foreign, resolution):
    """Blurays for BDMV regardless of resolution/foreign - it's intake-only
    and gets re-routed properly by preprocess.route_folder later, once the
    BDMV has been remuxed to a plain MKV (section 1.1)."""
    if source_type == "bdmv":
        return "Blurays"
    return preprocess.route_folder(is_foreign, resolution)


def move_to_staging(conn, movie_id):
    movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    if movie is None:
        raise RuntimeError(f"movie {movie_id} not found")
    if not movie["clean_title"] or not movie["year"]:
        raise RuntimeError("movie must be TMDb-matched before moving to staging")

    source = Path(movie["original_path"])
    category = category_for(movie["source_type"], movie["is_foreign"], movie["resolution"])
    folder_name = f"{movie['clean_title']} ({movie['year']})"
    destination_folder = Path(config.TORRENTS_ROOT) / category / folder_name

    if destination_folder.exists():
        conn.execute(
            "UPDATE movies SET collision = 1, collision_path = ?, updated_at = datetime('now') WHERE id = ?",
            (str(destination_folder), movie_id),
        )
        conn.commit()
        raise RuntimeError(f"destination already exists: {destination_folder}")

    destination_folder.parent.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        # BDMV package or a subfolder already containing the video - rename
        # the whole thing into place, keeping whatever's inside as-is.
        shutil.move(str(source), str(destination_folder))
        new_original_path = destination_folder
    else:
        # Loose file - wrap it in the IMDb-named folder, keeping its raw
        # filename untouched inside (Pre-processing renames the file itself).
        destination_folder.mkdir(parents=True)
        new_file_path = destination_folder / source.name
        shutil.move(str(source), str(new_file_path))
        new_original_path = new_file_path

    conn.execute(
        """
        UPDATE movies SET original_path = ?, status = 'discovered', collision = 0, collision_path = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (str(new_original_path), movie_id),
    )
    conn.commit()
    return new_original_path
