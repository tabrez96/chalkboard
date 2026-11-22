"""Test doubles for the model interface; no network, deterministic."""

import hashlib

from chalkboard.ai.base import EMBED_DIMENSIONS, Vector


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
