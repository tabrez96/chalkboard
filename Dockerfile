FROM python:3.15.2-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && gunicorn --bind 0.0.0.0:8000 --workers 2 'chalkboard:create_app()'"]
