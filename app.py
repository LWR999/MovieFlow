from flask import Flask, abort, redirect, render_template, request, url_for

import config
import db
import naming
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

    @app.route("/preprocessing")
    def preprocessing():
        return render_template("preprocessing.html")

    @app.route("/processing")
    def processing():
        return render_template("processing.html")

    @app.route("/library")
    def library():
        return render_template("library.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=config.FLASK_DEBUG)
