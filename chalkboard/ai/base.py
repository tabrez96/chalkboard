from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

EMBED_DIMENSIONS = 1024
Vector = list[float]


@runtime_checkable
class LLMClient(Protocol):
    def generate(self, prompt: str, schema: dict) -> dict: ...

    def chat(self, messages: list[dict[str, str]]) -> str: ...


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[Vector]: ...


@runtime_checkable
class ModelClient(LLMClient, Embedder, Protocol): ...


@dataclass(frozen=True)
class OllamaConfig:
    url: str
    embed_model: str
    write_model: str


class HealthState(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    MODEL_MISSING = "model_missing"


@dataclass(frozen=True)
class Health:
    state: HealthState
    missing: tuple[str, ...] = ()
    seconds: float | None = (
        None  # round-trip time of the check when the server answered
    )

    @property
    def pull_commands(self) -> list[str]:
        return [f"ollama pull {m}" for m in self.missing]


class OllamaError(Exception): ...


class OllamaUnreachable(OllamaError): ...


class FormatMismatch(OllamaError):
    """The server answered, but not in the requested JSON format."""


class ModelMissing(OllamaError):
    def __init__(self, model: str):
        super().__init__(f"Model {model} is not installed on the Ollama server")
        self.model = model
