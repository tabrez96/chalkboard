<!-- about -->

# Chalkboard

**Teacher-approved sources in, cited course plans out**

Website: [dmi.tabrez.xyz/p/chalkboard](https://dmi.tabrez.xyz/p/chalkboard)

## How to use it

Setting it up and using it, step by step: [the guide](design/guide.md).

## Screens

### S01 · Sign in

Sign in to the teacher's account

![S01 Sign in](design/screens/S01-sign-in/screen.png)

### S02 · Subjects

See every subject at a glance and create a new one

![S02 Subjects](design/screens/S02-subjects/screen.png)

### S03 · Sources

Add, review and remove the sources a subject's plans are written from

![S03 Sources](design/screens/S03-sources/screen.png)

### S04 · Overview

![S04 Overview](design/screens/S04-overview/screen.png)

### S05 · Chapters and classes

Write, approve and split each chapter into class-length lessons

![S05 Chapters and classes](design/screens/S05-chapters/screen.png)

### S06 · Today

Teach today's lesson from its plan, with its sources and an assistant

![S06 Today](design/screens/S06-today/screen.png)

### S07 · Students

Keep the class list for a subject

![S07 Students](design/screens/S07-students/screen.png)

### S08 · Settings

Connect the Ollama server, choose allowed websites, theme, backups and deletion

![S08 Settings](design/screens/S08-settings/screen.png)

### S09 · Teacher Guide

![S09 Teacher Guide](design/screens/S09-teacher-guide/screen.png)
<!-- /about -->

## Development

Prerequisites: Docker with Compose, uv 0.13.9, Node.js (for `npx`).

- Install dependencies: `uv sync`
- Run everything: `docker compose up --build`, then http://localhost:8000/healthz returns `{"status": "ok"}`.
- Run the app locally against the Compose database: `docker compose up -d db`, `uv run alembic upgrade head`, `uv run flask --app chalkboard run --debug`
  - If `.env` sets a custom `POSTGRES_PASSWORD`, first run `export CHALKBOARD_DATABASE_URL="postgresql+psycopg://chalkboard:<POSTGRES_PASSWORD>@localhost:5432/chalkboard"`. Host commands don't read `.env`.
- New migration: `uv run alembic revision --autogenerate -m "<what changes>"`; apply it with `uv run alembic upgrade head`.
- Tests: `docker compose up -d db`, then `uv run pytest` (creates and drops `chalkboard_test`; override the URL with `CHALKBOARD_TEST_DATABASE_URL`).
- Create the first account: `uv run flask --app chalkboard create-teacher --email you@example.com --name "Your name"` (with Compose: `docker compose exec web flask --app chalkboard create-teacher ...`). It asks for the password twice.
- Reset a password: `uv run flask --app chalkboard reset-password --email you@example.com`.
- Fonts: Inter and Raleway are bundled in `chalkboard/static/fonts/`; see its `README.md` for the source packages and licences.
- Format: `uv run ruff format . && npx prettier@3.10.1 --write .`
- Checks (same as CI): `uv run ruff format --check . && uv run ruff check .` and `npx prettier@3.10.1 --check .`

Configuration (environment variables):

- `CHALKBOARD_DATABASE_URL`: SQLAlchemy URL of the PostgreSQL database.
- `CHALKBOARD_SECRET_KEY`: key that signs sessions.
- `CHALKBOARD_DATA_DIR`: where uploaded files and other data are stored.
- `CHALKBOARD_BACKUP_DIR`: where backups are written.

Set a real `CHALKBOARD_SECRET_KEY` and `POSTGRES_PASSWORD` in a `.env` file next to `compose.yaml` for anything but local development. The password is placed inside the database URL, so use URL-safe values (no `@`, `:` or `/`): generate both with `openssl rand -hex 32`.

---

<sub>Created with ❤️ by Dunder Mifflin agents</sub>
