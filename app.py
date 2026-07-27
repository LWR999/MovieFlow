from flask import Flask, render_template

import config
import db
import scanner


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
