from flask import current_app

from chalkboard.ai.base import (
    Embedder,
    Health,
    HealthState,
    LLMClient,
    OllamaConfig,
)
from chalkboard.ai.ollama import OllamaClient
from chalkboard.models import Teacher

__all__ = [
    "TRANSPORT_KEY",
    "Embedder",
    "Health",
    "HealthState",
    "LLMClient",
    "OllamaClient",
    "OllamaConfig",
    "client_for",
    "config_for",
    "record_status",
]

TRANSPORT_KEY = "chalkboard.transport"  # tests put an httpx.MockTransport here


def config_for(teacher: Teacher) -> OllamaConfig:
    s = current_app.extensions["chalkboard.settings"]
    return OllamaConfig(
        teacher.ollama_url or s.ollama_url,
        teacher.embed_model or s.embed_model,
        teacher.write_model or s.write_model,
    )


def client_for(config: OllamaConfig) -> OllamaClient:
    return OllamaClient(config, transport=current_app.extensions.get(TRANSPORT_KEY))


def record_status(teacher: Teacher, health: Health) -> None:
    teacher.ollama_status = health.state.value  # caller commits
