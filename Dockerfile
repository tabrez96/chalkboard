FROM python:3.15.2-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/
# pg_dump must be at least the server's major version (19); Debian's own client is older.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl postgresql-common \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends postgresql-client-19 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && gunicorn --bind 0.0.0.0:8000 --workers 2 'chalkboard:create_app()'"]
