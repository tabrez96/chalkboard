from datetime import timedelta

from flask import Flask
from flask_wtf import CSRFProtect

from chalkboard import (
    ai,
    auth,
    cli,
    health,
    jobs,
    settings_page,
    shell,
    students,
    subjects,
)
from chalkboard.config import Settings
from chalkboard.db import init_db

csrf = CSRFProtect()


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
    app.extensions["chalkboard.settings"] = settings
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # refuse oversized uploads
    init_db(app, settings.database_url)
    ai.init_app(app)
    csrf.init_app(app)
    app.register_blueprint(health.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(subjects.bp)
    app.register_blueprint(students.bp)
    app.register_blueprint(settings_page.bp)
    app.register_blueprint(jobs.bp)
    shell.init_shell(app)
    app.before_request(auth.require_sign_in)
    app.cli.add_command(cli.create_teacher)
    app.cli.add_command(cli.reset_password)
    app.cli.add_command(cli.worker)
    return app
