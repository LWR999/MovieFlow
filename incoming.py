import shutil
from pathlib import Path

import config
import preprocess
import probe
import scanner


def discover_incoming_items(root=None):
    root = Path(root if root is not None else config.TORRENTS_ROOT_INCOMING)
    if not root.is_dir():
        return []
    return scanner.scan_folder_entries(root, skip_names={"TV"})


def sync_incoming(conn):
    existing = {row["original_path"] for row in conn.execute("SELECT original_path FROM movies")}
    inserted = 0
    for item in discover_incoming_items():
        if item["original_path"] in existing:
            continue
        title, year = scanner.parse_title_year(item["raw_name"])
        inspection = probe.inspect_media(item["original_path"], item["source_type"])
        conn.execute(
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

    source = Path(movie["original_path"])
    category = category_for(movie["source_type"], movie["is_foreign"], movie["resolution"])
    destination = Path(config.TORRENTS_ROOT) / category / source.name

    if destination.exists():
        conn.execute(
            "UPDATE movies SET collision = 1, updated_at = datetime('now') WHERE id = ?",
            (movie_id,),
        )
        conn.commit()
        raise RuntimeError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))

    conn.execute(
        """
        UPDATE movies SET original_path = ?, status = 'discovered', collision = 0,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (str(destination), movie_id),
    )
    conn.commit()
    return destination
