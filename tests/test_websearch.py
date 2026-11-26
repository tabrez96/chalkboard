import httpx
import pytest

from chalkboard.ai import websearch
from chalkboard.ai.fake import FakeLLM
from chalkboard.ai.websearch import WebSearchError

SEARXNG = "http://searxng.test:8080"


def results(*items):
    return {"results": list(items)}


def hit(url, content="A snippet.", title="A title"):
    return {"url": url, "content": content, "title": title}


def transport(response, seen=None):
    def answer(request):
        if seen is not None:
            seen.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    return httpx.MockTransport(answer)


def test_search_sends_json_query():
    seen = []
    reply = httpx.Response(200, json=results(hit("https://a.test/x")))
    found = websearch.search("sprint speed", SEARXNG, transport=transport(reply, seen))
    [request] = seen
    assert (request.url.host, request.url.port, request.url.path) == (
        "searxng.test",
        8080,
        "/search",
    )
    assert request.url.params["format"] == "json"
    assert request.url.params["q"] == "sprint speed"
    assert [r.url for r in found] == ["https://a.test/x"]


def test_parse_results_filters_and_labels():
    data = results(
        hit("https://a.test/1", "  one \n two  ", " Title\n One "),
        hit("https://a.test/nosnippet", "  "),
        hit("javascript:alert(1)"),
        "not a dict",
        {"url": 5, "content": "x"},
        hit("https://a.test/1", "repeat"),
        {"url": "https://a.test/2", "content": "x" * 900},
        hit("https://a.test/3"),
    )
    found = websearch.parse_results(data, limit=3)
    assert [r.label for r in found] == ["W1", "W2", "W3"]
    assert found[0].snippet == "one two" and found[0].title == "Title One"
    assert found[1].title == "https://a.test/2"  # no title: the URL
    assert len(found[1].snippet) == websearch.SNIPPET_MAX
    assert len(websearch.parse_results(data, limit=2)) == 2


@pytest.mark.parametrize("data", [None, [], {"results": "x"}, {}])
def test_parse_results_unusable_data(data):
    assert websearch.parse_results(data) == []


@pytest.mark.parametrize(
    "reply",
    [
        httpx.ConnectError("down"),
        httpx.Response(500),
        httpx.Response(200, content=b"<html>not json"),
    ],
)
def test_search_errors(reply):
    with pytest.raises(WebSearchError):
        websearch.search("q", SEARXNG, transport=transport(reply))


def test_write_web_answer_keeps_cited_sentences():
    found = [
        websearch.WebResult("W1", "One", "https://a.test/1", "s1"),
        websearch.WebResult("W2", "Two", "https://a.test/2", "s2"),
    ]
    llm = FakeLLM(
        [
            {
                "sentences": [
                    {"text": " Cited  two. ", "results": [" w2 ", "W2", "W1"]},
                    {"text": "Uncited.", "results": []},
                    {"text": "Unknown label.", "results": ["W9", 4]},
                    {"text": "   ", "results": ["W1"]},
                    {"text": "No list.", "results": "W1"},
                    "junk",
                ]
            }
        ]
    )
    assert websearch.write_web_answer(llm, "Question?", found) == [
        {"text": "Cited two.", "links": ["https://a.test/2", "https://a.test/1"]}
    ]
    assert "[W1] One (https://a.test/1)\ns1" in llm.calls[0][1]


def test_write_web_answer_malformed_and_empty():
    found = [websearch.WebResult("W1", "One", "https://a.test/1", "s1")]
    assert websearch.write_web_answer(FakeLLM([{"sentences": "x"}]), "Q", found) == []
    assert websearch.write_web_answer(FakeLLM(["text"]), "Q", found) == []
    llm = FakeLLM()
    assert websearch.write_web_answer(llm, "Q", []) == [] and llm.calls == []
