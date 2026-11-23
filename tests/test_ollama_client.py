import json

import httpx
import pytest

from chalkboard.ai import HealthState, OllamaConfig
from chalkboard.ai.base import ModelMissing, OllamaError, OllamaUnreachable
from chalkboard.ai.ollama import OllamaClient

CONFIG = OllamaConfig("http://ollama.test:11434", "qwen3-embedding:0.6b", "qwen3.5:9b")


def make(handler, config=CONFIG):
    return OllamaClient(config, transport=httpx.MockTransport(handler))


def chat_answer(content):
    return httpx.Response(
        200, json={"message": {"role": "assistant", "content": content}}
    )


def tags(*names):
    return httpx.Response(
        200, json={"models": [{"name": n, "model": n} for n in names]}
    )


def test_chat_returns_content():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return chat_answer("hello")

    messages = [{"role": "user", "content": "hi"}]
    assert make(handler).chat(messages) == "hello"
    assert seen[0]["model"] == "qwen3.5:9b"
    assert seen[0]["stream"] is False
    assert seen[0]["messages"] == messages


def test_generate_sends_schema_and_parses_json():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return chat_answer('{"n": 3}')

    assert make(handler).generate("count", schema) == {"n": 3}
    assert seen[0]["format"] == schema
    assert seen[0]["messages"] == [{"role": "user", "content": "count"}]


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_generate_bad_json_raises(content):
    with pytest.raises(OllamaError):
        make(lambda r: chat_answer(content)).generate("x", {})


def test_chat_unreadable_answer_raises():
    with pytest.raises(OllamaError):
        make(lambda r: httpx.Response(200, json={"oops": 1})).chat([])
    with pytest.raises(OllamaError):
        make(lambda r: httpx.Response(200, text="<html>")).chat([])


def test_embed_sends_dimensions():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.0] * 1024, [1.0] * 1024]})

    vectors = make(handler).embed(["a", "b"])
    assert len(vectors) == 2 and len(vectors[0]) == 1024
    assert seen[0] == {
        "model": "qwen3-embedding:0.6b",
        "input": ["a", "b"],
        "dimensions": 1024,
    }


@pytest.mark.parametrize("embeddings", [[[0.0] * 512], [[0.0] * 1024] * 2, None])
def test_embed_wrong_shape_raises(embeddings):
    with pytest.raises(OllamaError):
        make(lambda r: httpx.Response(200, json={"embeddings": embeddings})).embed(
            ["a"]
        )


def test_embed_empty_makes_no_request():
    def handler(request):
        raise AssertionError("no request expected")

    assert make(handler).embed([]) == []


def test_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(OllamaUnreachable):
        make(handler).chat([])


def test_model_not_found_raises_model_missing():
    error = {"error": 'model "qwen3.5:9b" not found, try pulling it first'}
    with pytest.raises(ModelMissing) as info:
        make(lambda r: httpx.Response(404, json=error)).chat([])
    assert info.value.model == "qwen3.5:9b"


def test_server_error_raises():
    with pytest.raises(OllamaError, match="500"):
        make(lambda r: httpx.Response(500, json={"error": "boom"})).chat([])
    with pytest.raises(OllamaError, match="404"):
        make(lambda r: httpx.Response(404, text="nope")).chat([])


def test_health_ok():
    health = make(lambda r: tags("qwen3-embedding:0.6b", "qwen3.5:9b")).health()
    assert health.state is HealthState.OK
    assert isinstance(health.seconds, float)


def test_health_model_missing():
    health = make(lambda r: tags("qwen3.5:9b")).health()
    assert health.state is HealthState.MODEL_MISSING
    assert health.missing == ("qwen3-embedding:0.6b",)
    assert health.pull_commands == ["ollama pull qwen3-embedding:0.6b"]


def test_health_same_model_reported_once():
    config = OllamaConfig(CONFIG.url, "same:1b", "same:1b")
    assert make(lambda r: tags(), config).health().missing == ("same:1b",)


def test_health_untagged_name_matches_latest():
    config = OllamaConfig(CONFIG.url, "qwen3-embedding", "qwen3.5:9b")
    ok = make(lambda r: tags("qwen3-embedding:latest", "qwen3.5:9b"), config).health()
    assert ok.state is HealthState.OK
    other = make(lambda r: tags("qwen3-embedding:0.6b", "qwen3.5:9b"), config).health()
    assert other.missing == ("qwen3-embedding",)


def test_health_registry_port_is_not_a_tag():
    name = "registry.local:5000/acme/embed"
    config = OllamaConfig(CONFIG.url, name, "qwen3.5:9b")
    ok = make(lambda r: tags(f"{name}:latest", "qwen3.5:9b"), config).health()
    assert ok.state is HealthState.OK


def test_health_unreachable():
    def down(request):
        raise httpx.ConnectError("down")

    assert make(down).health().state is HealthState.UNREACHABLE
    assert make(lambda r: httpx.Response(500)).health().state is HealthState.UNREACHABLE
    html = make(lambda r: httpx.Response(200, text="<html>")).health()
    assert html.state is HealthState.UNREACHABLE


def test_health_malformed_model_list_is_unreachable():
    for models in (1, "qwen3.5:9b", {"name": "x"}):
        client = make(lambda r, m=models: httpx.Response(200, json={"models": m}))
        assert client.health().state is HealthState.UNREACHABLE


def test_health_empty_model_list_reports_missing():
    health = make(lambda r: tags()).health()
    assert health.state is HealthState.MODEL_MISSING
    assert health.missing == ("qwen3-embedding:0.6b", "qwen3.5:9b")
