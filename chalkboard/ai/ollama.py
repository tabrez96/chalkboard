import json
import time

import httpx

from chalkboard.ai.base import (
    EMBED_DIMENSIONS,
    Health,
    HealthState,
    ModelMissing,
    OllamaConfig,
    OllamaError,
    OllamaUnreachable,
    Vector,
)
from chalkboard.net import guarded_client

TIMEOUT = httpx.Timeout(
    600.0, connect=5.0
)  # generation on CPU is slow; connecting is not
HEALTH_TIMEOUT = httpx.Timeout(5.0)
UNREADABLE = "Ollama sent an unreadable answer"


def _full(name: str) -> str:
    # A ":" before the last "/" is a registry port, not a tag.
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return response.text[:200]


class OllamaClient:
    """Talks to the Ollama server; implements both `LLMClient` and `Embedder`."""

    def __init__(
        self, config: OllamaConfig, transport: httpx.BaseTransport | None = None
    ):
        self.config = config
        self.http = guarded_client(
            [config.url], transport=transport, base_url=config.url, timeout=TIMEOUT
        )

    def _post(self, path: str, body: dict) -> dict:
        try:
            response = self.http.post(path, json=body)
        except httpx.TransportError as exc:
            raise OllamaUnreachable(str(exc)) from exc
        if response.status_code == 404 and "not found" in _error_text(response):
            raise ModelMissing(body["model"])
        if not response.is_success:
            raise OllamaError(
                f"Ollama returned {response.status_code}: {_error_text(response)}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError(UNREADABLE) from exc
        if not isinstance(data, dict):
            raise OllamaError(UNREADABLE)
        return data

    def _chat(self, body: dict) -> str:
        data = self._post("/api/chat", body)
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaError(UNREADABLE) from exc
        if not isinstance(content, str):
            raise OllamaError(UNREADABLE)
        return content

    def chat(self, messages: list[dict[str, str]]) -> str:
        return self._chat(
            {"model": self.config.write_model, "messages": messages, "stream": False}
        )

    def generate(self, prompt: str, schema: dict) -> dict:
        content = self._chat(
            {
                "model": self.config.write_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
            }
        )
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            result = None
        if not isinstance(result, dict):
            raise OllamaError("The model's answer did not match the requested format")
        return result

    def embed(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        data = self._post(
            "/api/embed",
            {
                "model": self.config.embed_model,
                "input": texts,
                "dimensions": EMBED_DIMENSIONS,
            },
        )
        vectors = data.get("embeddings")
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(texts)
            or any(
                not isinstance(v, list) or len(v) != EMBED_DIMENSIONS for v in vectors
            )
        ):
            raise OllamaError(
                f"Ollama did not return {len(texts)} vectors of {EMBED_DIMENSIONS} numbers"
            )
        return vectors

    def health(self) -> Health:
        start = time.perf_counter()
        try:
            response = self.http.get("/api/tags", timeout=HEALTH_TIMEOUT)
            data = response.json() if response.is_success else None
        except httpx.TransportError, ValueError:
            data = None
        if not isinstance(data, dict):
            return Health(HealthState.UNREACHABLE)
        models = data.get("models")
        if models is None:
            models = []
        if not isinstance(models, list):
            return Health(HealthState.UNREACHABLE)
        installed = set()
        for entry in models:
            if isinstance(entry, dict):
                for key in ("name", "model"):
                    if isinstance(entry.get(key), str):
                        installed.add(_full(entry[key]))
        wanted = dict.fromkeys([self.config.embed_model, self.config.write_model])
        missing = tuple(m for m in wanted if _full(m) not in installed)
        seconds = round(time.perf_counter() - start, 1)
        state = HealthState.MODEL_MISSING if missing else HealthState.OK
        return Health(state, missing, seconds)

    def close(self) -> None:
        self.http.close()
