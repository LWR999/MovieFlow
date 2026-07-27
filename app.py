from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

import config
import db
import jobs
import naming
import preprocess
import processing as audio_processing
import scanner
import tmdb


def create_app():
    app = Flask(__name__)
    app.config["DEBUG"] = config.FLASK_DEBUG

    db.init_app(app)

    @app.route("/")
    def intake():
        conn = db.get_db()
        scanner.sync_discovered(conn)
        movies = conn.execute(
            "SELECT * FROM movies WHERE status = 'discovered' ORDER BY created_at DESC"
        ).fetchall()
        return render_template("intake.html", movies=movies)

    @app.route("/movies/<int:movie_id>/match")
    def match(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)

        query_title = request.args.get("title", movie["title"] or "")
        query_year = request.args.get("year", movie["year"] or "")

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

        try:
            external_ids = tmdb.get_external_ids(tmdb_id)
        except tmdb.TMDbError as e:
            return render_template(
                "match.html",
                movie=movie,
                query_title=title,
                query_year=year or "",
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
        return redirect(url_for("intake"))

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

    @app.route("/movies/<int:movie_id>/preprocess", methods=["POST"])
    def trigger_preprocess(movie_id):
        conn = db.get_db()
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            abort(404)
        if not movie["tmdb_id"]:
            abort(400, "movie must be TMDb-matched before pre-processing")

        if movie["source_type"] == "bdmv":
            return redirect(url_for("bdmv_playlists", movie_id=movie_id))

        try:
            jobs.submit_job(movie_id, "preprocess", preprocess.preprocess_mp4)
        except jobs.JobAlreadyRunning:
            pass
        return redirect(url_for("preprocessing"))

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
        return redirect(url_for("preprocessing"))

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

    @app.route("/preprocessing")
    def preprocessing():
        conn = db.get_db()
        job_rows = conn.execute(
            """
            SELECT jobs.*, movies.title, movies.clean_title, movies.year
            FROM jobs JOIN movies ON jobs.movie_id = movies.id
            WHERE jobs.job_type = 'preprocess' AND jobs.status IN ('queued', 'running', 'error')
            ORDER BY jobs.id DESC
            """
        ).fetchall()
        return render_template("preprocessing.html", jobs=job_rows)

    @app.route("/processing")
    def processing():
        conn = db.get_db()
        movies = conn.execute(
            "SELECT * FROM movies WHERE status = 'preprocessed' ORDER BY created_at DESC"
        ).fetchall()
        job_rows = conn.execute(
            """
            SELECT jobs.*, movies.clean_title, movies.year
            FROM jobs JOIN movies ON jobs.movie_id = movies.id
            WHERE jobs.job_type = 'process' AND jobs.status IN ('queued', 'running', 'error')
            ORDER BY jobs.id DESC
            """
        ).fetchall()
        return render_template("processing.html", movies=movies, jobs=job_rows)

    @app.route("/movies/process", methods=["POST"])
    def process_selected():
        movie_ids = request.form.getlist("movie_id", type=int)
        for movie_id in movie_ids:
            try:
                jobs.submit_job(movie_id, "process", audio_processing.process_audio)
            except jobs.JobAlreadyRunning:
                pass
        return redirect(url_for("processing"))

    @app.route("/library")
    def library():
        return render_template("library.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=config.FLASK_DEBUG)
