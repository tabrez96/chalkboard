import httpx

from chalkboard.ai import Embedder, LLMClient, OllamaClient, OllamaConfig
from chalkboard.ai.fake import FakeEmbedder, FakeLLM


def test_fakes_implement_protocols():
    real = OllamaClient(
        OllamaConfig("http://ollama.test:11434", "e", "w"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    for obj in (FakeLLM(), real):
        assert isinstance(obj, LLMClient)
    for obj in (FakeEmbedder(), real):
        assert isinstance(obj, Embedder)


def test_fake_embedder_is_deterministic():
    fake = FakeEmbedder()
    a, b, again = fake.embed(["a", "b", "a"])
    assert len(a) == 1024 and all(isinstance(x, float) for x in a)
    assert a == again and a != b
    assert fake.calls == [["a", "b", "a"]]


def test_fake_llm_returns_queued_responses():
    llm = FakeLLM([{"x": 1}, "hi"])
    assert llm.generate("p", {}) == {"x": 1}
    assert llm.chat([{"role": "user", "content": "q"}]) == "hi"
    assert llm.generate("p", {}) == {}
    assert llm.chat([]) == "ok"
    assert [kind for kind, _ in llm.calls] == ["generate", "chat", "generate", "chat"]
