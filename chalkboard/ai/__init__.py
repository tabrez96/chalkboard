from flask import Flask, current_app
from sqlalchemy import select

from chalkboard.ai.base import (
    Embedder,
    Health,
    HealthState,
    LLMClient,
    OllamaConfig,
)
from chalkboard.ai.ollama import OllamaClient
from chalkboard.ai.redact import RedactingClient, Redactor
from chalkboard.db import get_session
from chalkboard.models import Student, Subject, Teacher

__all__ = [
    "FACTORY_KEY",
    "TRANSPORT_KEY",
    "Embedder",
    "Health",
    "HealthState",
    "LLMClient",
    "OllamaConfig",
    "RedactingClient",
    "Redactor",
    "config_for",
    "init_app",
    "model_client",
    "names_for",
    "record_status",
]

TRANSPORT_KEY = "chalkboard.transport"  # tests put an httpx.MockTransport here
FACTORY_KEY = "chalkboard.model_factory"


def config_for(teacher: Teacher) -> OllamaConfig:
    s = current_app.extensions["chalkboard.settings"]
    return OllamaConfig(
        teacher.ollama_url or s.ollama_url,
        teacher.embed_model or s.embed_model,
        teacher.write_model or s.write_model,
    )


def _build(config: OllamaConfig, redactor: Redactor) -> RedactingClient:
    inner = OllamaClient(config, transport=current_app.extensions.get(TRANSPORT_KEY))
    return RedactingClient(inner, redactor)


def init_app(app: Flask) -> None:
    app.extensions[FACTORY_KEY] = _build


def names_for(teacher: Teacher, subject: Subject | None) -> Redactor:
    # A subject's own students; with no subject, every student of the account.
    query = select(Student.name).order_by(Student.id)
    if subject is not None:
        query = query.where(Student.subject_id == subject.id)
    else:
        query = query.join(Subject).where(Subject.teacher_id == teacher.id)
    return Redactor(list(get_session().scalars(query)), teacher.name)


def model_client(
    teacher: Teacher, subject: Subject | None = None, config: OllamaConfig | None = None
) -> RedactingClient:
    factory = current_app.extensions.get(FACTORY_KEY)
    if factory is None:
        raise RuntimeError("Model access is not set up: call ai.init_app in create_app")
    client = factory(config or config_for(teacher), names_for(teacher, subject))
    if not isinstance(client, RedactingClient):
        raise RuntimeError(  # noqa: TRY004
            "Model clients must be wrapped in RedactingClient"
        )
    return client


def record_status(teacher: Teacher, health: Health) -> None:
    teacher.ollama_status = health.state.value  # caller commits
