from flask import Flask, render_template

import config
import db


def create_app():
    app = Flask(__name__)
    app.config["DEBUG"] = config.FLASK_DEBUG

    db.init_app(app)

    @app.route("/")
    def intake():
        return render_template("intake.html")

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
