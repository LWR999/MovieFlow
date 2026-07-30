import os
import shutil
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

import config
import db
import incoming as incoming_stage
import jobs
import library as library_job
import naming
import preprocess
import processing as audio_processing
import queries
import scanner
import tmdb

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w200"

_DETAIL_BACK_ENDPOINT = {
    "incoming": "intake",
    "discovered": "workbench",
    "preprocessed": "workbench",
    "processed": "library_queue",
    "in_library": "library_queue",
}


def create_app():
    app = Flask(__name__)
    app.config["DEBUG"] = config.FLASK_DEBUG

    db.init_app(app)

    def _sync(conn):
        incoming_stage.sync_incoming(conn)
        scanner.sync_discovered(conn)

    @app.route("/posters/<path:poster_path>")
    def poster(poster_path):
        filename = os.path.basename(poster_path)
        cache_path = Path(config.POSTER_CACHE_DIR) / filename

        if not cache_path.exists():
            try:
                resp = requests.get(f"{TMDB_IMAGE_BASE}/{filename}", timeout=10)
                resp.raise_for_status()
            except requests.RequestException:
                abort(502, "could not fetch poster")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(resp.content)

        return send_file(cache_path, mimetype="image/jpeg")

    @app.route("/")
    def intake():
        conn = db.get_db()
        _sync(conn)
        movies = queries.intake_movies(conn)
        return render_template("intake.html", movies=movies)

    @app.route("/workbench")
    def workbench():
        conn = db.get_db()
        _sync(conn)
        movies = queries.workbench_movies(conn)
        return render_template("workbench.html", movies=movies)

    @app.route("/library-queue")
    def library_queue():
        conn = db.get_db()
        movies = queries.library_queue_movies(conn)
        return render_template("library_queue.html", movies=movies)

    @app.route("/library-queue/commit", methods=["POST"])
    def commit_library():
        conn = db.get_db()
        for movie in queries.library_queue_movies(conn):
            if movie["state"] == "in_progress":
                continue
            try:
                jobs.submit_job(movie["id"], "library_move", library_job.library_move)
            except jobs.JobAlreadyRunning:
                pass
        return redirect(url_for("library_queue"))

    @app.route("/movies/<int:movie_id>")
    def movie_detail(movie_id):
        conn = db.get_db()
        movie = queries.movie_with_job(conn, movie_id)
        if movie is None:
            abort(404)
        return render_template(
            "movie_detail.html", movie=movie, back_endpoint=_DETAIL_BACK_ENDPOINT[movie["status"]]
        )

    @app.route("/movies/<int:movie_id>/advance", methods=["POST"])
    def advance_movie(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)

        if movie["status"] == "incoming":
            try:
                incoming_stage.move_to_staging(conn, movie_id)
            except RuntimeError:
                pass  # collision recorded on the movie row; detail page shows it
        elif movie["status"] == "discovered":
            if movie["source_type"] == "bdmv":
                return redirect(url_for("bdmv_playlists", movie_id=movie_id))
            try:
                jobs.submit_job(movie_id, "preprocess", preprocess.preprocess_mp4)
            except jobs.JobAlreadyRunning:
                pass
        elif movie["status"] == "preprocessed":
            try:
                jobs.submit_job(movie_id, "process", audio_processing.process_audio)
            except jobs.JobAlreadyRunning:
                pass
        elif movie["status"] == "processed":
            try:
                jobs.submit_job(movie_id, "library_move", library_job.library_move)
            except jobs.JobAlreadyRunning:
                pass

        return redirect(url_for("movie_detail", movie_id=movie_id))

    @app.route("/movies/<int:movie_id>/match")
    def match(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)

        query_title = request.args.get("title", movie["title"] or "")
        query_year = request.args.get("year", movie["year"] or "")
        return_to = request.args.get("return_to") or request.referrer or url_for("movie_detail", movie_id=movie_id)

        candidates = []
        error = None
        if query_title.strip():
            try:
                candidates = tmdb.search_movies(query_title, query_year or None)
            except tmdb.TMDbError as e:
                error = str(e)

        return render_template(
            "match.html",
            movie=movie,
            query_title=query_title,
            query_year=query_year,
            return_to=return_to,
            candidates=candidates,
            error=error,
        )

    @app.route("/movies/<int:movie_id>/match/confirm", methods=["POST"])
    def confirm_match(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)

        tmdb_id = request.form["tmdb_id"]
        title = request.form["title"]
        year = request.form.get("year") or None
        poster_path = request.form.get("poster_path") or None
        original_language = request.form.get("original_language") or None
        return_to = request.form.get("return_to") or url_for("movie_detail", movie_id=movie_id)

        try:
            external_ids = tmdb.get_external_ids(tmdb_id)
        except tmdb.TMDbError as e:
            return render_template(
                "match.html",
                movie=movie,
                query_title=title,
                query_year=year or "",
                return_to=return_to,
                candidates=[],
                error=f"Could not confirm match: {e}",
            ), 502

        clean = naming.clean_title(title)
        is_foreign = 1 if original_language and original_language != "en" else 0

        conn.execute(
            """
            UPDATE movies
            SET tmdb_id = ?, imdb_id = ?, title = ?, year = ?, clean_title = ?,
                original_language = ?, poster_path = ?, is_foreign = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                tmdb_id, external_ids["imdb_id"], title, year, clean,
                original_language, poster_path, is_foreign, movie_id,
            ),
        )
        conn.commit()
        return redirect(return_to)

    @app.route("/movies/<int:movie_id>/foreign", methods=["POST"])
    def set_foreign(movie_id):
        conn = db.get_db()
        is_foreign = 1 if request.form.get("is_foreign") == "1" else 0
        conn.execute(
            "UPDATE movies SET is_foreign = ?, updated_at = datetime('now') WHERE id = ?",
            (is_foreign, movie_id),
        )
        conn.commit()
        return ("", 204)

    @app.route("/movies/<int:movie_id>/delete", methods=["POST"])
    def delete_movie(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)

        active_job = conn.execute(
            "SELECT id FROM jobs WHERE movie_id = ? AND status IN ('queued', 'running')",
            (movie_id,),
        ).fetchone()
        if active_job:
            abort(409, "cannot delete while a job is in progress for this movie")

        path_str = movie["final_path"] or movie["original_path"]
        path = Path(path_str)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

        conn.execute("DELETE FROM jobs WHERE movie_id = ?", (movie_id,))
        conn.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
        conn.commit()

        return redirect(request.referrer or url_for("intake"))

    @app.route("/movies/<int:movie_id>/preprocess/bdmv")
    def bdmv_playlists(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)
        if movie["source_type"] != "bdmv":
            abort(400, "movie is not a BDMV package")

        playlists = preprocess.list_bdmv_playlists(movie["original_path"])
        for pl in playlists:
            pl["duration_display"] = preprocess.format_duration(pl["duration_seconds"])
            pl["size_display"] = preprocess.format_size(pl["estimated_bytes"])

        return render_template("bdmv_playlists.html", movie=movie, playlists=playlists)

    @app.route("/movies/<int:movie_id>/preprocess/bdmv/confirm", methods=["POST"])
    def confirm_bdmv_playlist(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)

        playlist_filename = request.form["playlist"]
        try:
            jobs.submit_job(movie_id, "preprocess", preprocess.preprocess_bdmv, playlist_filename)
        except jobs.JobAlreadyRunning:
            pass
        return redirect(url_for("movie_detail", movie_id=movie_id))

    @app.route("/jobs/<int:job_id>/dismiss", methods=["POST"])
    def dismiss_job(job_id):
        conn = db.get_db()
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            abort(404)
        if job["status"] not in ("error", "done"):
            abort(409, "cannot dismiss a job that is still queued or running")
        movie_id = job["movie_id"]
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return redirect(request.referrer or url_for("movie_detail", movie_id=movie_id))

    @app.route("/api/jobs/<int:job_id>")
    def get_job(job_id):
        conn = db.get_db()
        job = jobs.get_job(conn, job_id)
        if job is None:
            abort(404)
        return jsonify(dict(job))

    @app.route("/api/jobs")
    def list_jobs():
        conn = db.get_db()
        movie_id = request.args.get("movie_id", type=int)
        if movie_id is not None:
            rows = jobs.get_jobs_for_movie(conn, movie_id)
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
        return jsonify([dict(r) for r in rows])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=config.FLASK_DEBUG)
