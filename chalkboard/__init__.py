from flask import Flask

from chalkboard import health
from chalkboard.config import Settings
from chalkboard.db import init_db


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.extensions["chalkboard.settings"] = settings
    init_db(app, settings.database_url)
    app.register_blueprint(health.bp)
    return app
