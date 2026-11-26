"""Web search for the assistant: SearXNG's JSON API through the outbound guard, and answers written from its results."""

from dataclasses import dataclass

from httpx import BaseTransport, Client, TransportError

from chalkboard.ai.base import LLMClient
from chalkboard.net import guarded_client

WEB_K = 5
SEARCH_TIMEOUT = 15.0
SNIPPET_MAX = 500


class WebSearchError(Exception):
    """SearXNG could not be reached or sent an unreadable answer."""


@dataclass(frozen=True)
class WebResult:
    label: str  # "W1" … "Wn", in SearXNG's order
    title: str
    url: str
    snippet: str


def searxng_client(base_url: str, transport: BaseTransport | None = None) -> Client:
    return guarded_client(
        [base_url], transport=transport, base_url=base_url, timeout=SEARCH_TIMEOUT
    )


def parse_results(data: object, limit: int = WEB_K) -> list[WebResult]:
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    results: list[WebResult] = []
    seen: set[str] = set()
    for item in items:
        if len(results) >= limit:
            break
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        content = item.get("content")
        snippet = " ".join(content.split()) if isinstance(content, str) else ""
        if not snippet or url in seen:
            continue
        seen.add(url)
        title = item.get("title")
        title = " ".join(title.split()) if isinstance(title, str) else ""
        label = f"W{len(results) + 1}"
        results.append(WebResult(label, title or url, url, snippet[:SNIPPET_MAX]))
    return results


def search(
    query: str,
    base_url: str,
    *,
    transport: BaseTransport | None = None,
    limit: int = WEB_K,
) -> list[WebResult]:
    with searxng_client(base_url, transport) as client:
        try:
            response = client.get(
                "/search",
                params={
                    "q": query,
                    "format": "json",
                    "safesearch": 1,
                    "categories": "general",
                },
            )
        except TransportError as exc:
            raise WebSearchError(str(exc)) from exc
    if response.status_code != 200:
        raise WebSearchError(f"SearXNG returned {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise WebSearchError("SearXNG sent an unreadable answer") from exc
    return parse_results(data, limit)


WEB_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "results": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "results"],
            },
        }
    },
    "required": ["sentences"],
}
WEB_PROMPT = (
    "{instruction}\n\nWrite only from the web results below. Return JSON: a list of sentences, each with the labels "
    'of the results it is based on, for example ["W2"]. Every sentence must cite at least one result. Leave out '
    "anything the results don't support.\n\n{results}"
)


def format_results(results: list[WebResult]) -> str:
    return "\n\n".join(f"[{r.label}] {r.title} ({r.url})\n{r.snippet}" for r in results)


def write_web_answer(
    client: LLMClient, instruction: str, results: list[WebResult]
) -> list[dict]:
    """Sentences citing at least one result, as [{"text": str, "links": [url, ...]}]; names not yet restored."""
    if not results:
        return []
    answer = client.generate(
        WEB_PROMPT.format(instruction=instruction, results=format_results(results)),
        WEB_SCHEMA,
    )
    items = answer.get("sentences") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return []
    urls = {r.label: r.url for r in results}
    parts = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = " ".join(item["text"].split())
        given = item.get("results")
        links: list[str] = []
        for label in given if isinstance(given, list) else []:
            url = urls.get(label.strip().upper()) if isinstance(label, str) else None
            if url is not None and url not in links:
                links.append(url)
        if text and links:
            parts.append({"text": text, "links": links})
    return parts
