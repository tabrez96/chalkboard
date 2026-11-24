"""Test doubles for the model interface; no network, deterministic."""

import hashlib

from chalkboard.ai.base import (
    EMBED_DIMENSIONS,
    Health,
    HealthState,
    OllamaUnreachable,
    Vector,
)


class FakeLLM:
    def __init__(self, responses: list[dict | str] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, object]] = []

    def generate(self, prompt: str, schema: dict) -> dict:
        self.calls.append(("generate", prompt))
        return self.responses.pop(0) if self.responses else {}

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(("chat", messages))
        return self.responses.pop(0) if self.responses else "ok"


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[Vector]:
        self.calls.append(texts)
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            vectors.append([seed[i % 32] / 255 for i in range(EMBED_DIMENSIONS)])
        return vectors


class FakeOllama(FakeEmbedder):
    """Stands in for the Ollama client behind RedactingClient; set `up = False` to simulate an unreachable server."""

    def __init__(self, up: bool = True, generated: list[dict] | None = None):
        super().__init__()
        self.up = up
        self.generated = list(generated or [])

    def embed(self, texts: list[str]) -> list[Vector]:
        if not self.up:
            raise OllamaUnreachable("down")
        return super().embed(texts)

    def generate(self, prompt: str, schema: dict) -> dict:
        if not self.up:
            raise OllamaUnreachable("down")
        self.calls.append([prompt])
        return self.generated.pop(0) if self.generated else {}

    def health(self) -> Health:
        return Health(HealthState.OK if self.up else HealthState.UNREACHABLE)

    def close(self) -> None:
        pass
