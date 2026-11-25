"""The writer: sentences tied to passages; an uncited sentence is retried once, then dropped."""

from dataclasses import dataclass

from chalkboard.ai.base import FormatMismatch, LLMClient
from chalkboard.ai.retrieve import Passage


@dataclass(frozen=True)
class DraftSentence:
    text: str
    chunk_ids: tuple[int, ...]
    item: int | None = (
        None  # index into the version's items; set by later generation tickets
    )
    check_flag: bool = False
    check_reason: str | None = None
    minutes: int | None = None  # minutes of a timed lesson step


SENTENCES_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "passages": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "passages"],
            },
        }
    },
    "required": ["sentences"],
}
WRITE_PROMPT = (
    "{instruction}\n\nWrite only from the passages below. Return JSON: a list of sentences, each with the labels "
    'of the passages it is based on, for example ["P2"]. Every sentence must cite at least one passage. Leave '
    "out anything the passages don't support.\n\n{passages}"
)
RETRY_PROMPT = (
    "Each sentence below needs the labels of the passages that support it. Return the same sentences in the same "
    "order, each with its labels; give an empty list for a sentence no passage supports.\n\nSentences:\n"
    "{sentences}\n\nPassages:\n{passages}"
)


def format_passages(passages: list[Passage]) -> str:
    return "\n\n".join(f"[{p.label}] {p.text}" for p in passages)


def parse_sentences(
    answer: object, labels: dict[str, int]
) -> list[tuple[str, tuple[int, ...]]]:
    items = answer.get("sentences") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = " ".join(item["text"].split())
        if not text:
            continue
        given = item.get("passages")
        ids: list[int] = []
        for label in given if isinstance(given, list) else []:
            chunk_id = (
                labels.get(label.strip().upper()) if isinstance(label, str) else None
            )
            if chunk_id is not None and chunk_id not in ids:
                ids.append(chunk_id)
        out.append((text, tuple(ids)))
    return out


def write_sentences(
    client: LLMClient, instruction: str, passages: list[Passage]
) -> list[DraftSentence]:
    if not passages:
        return []
    labels = {p.label: p.chunk_id for p in passages}
    shown = format_passages(passages)
    first = parse_sentences(
        client.generate(
            WRITE_PROMPT.format(instruction=instruction, passages=shown),
            SENTENCES_SCHEMA,
        ),
        labels,
    )
    uncited = [i for i, (_, ids) in enumerate(first) if not ids]
    retried: list[tuple[str, tuple[int, ...]]] = []
    if uncited:
        numbered = "\n".join(f"{n + 1}. {first[i][0]}" for n, i in enumerate(uncited))
        try:
            retried = parse_sentences(
                client.generate(
                    RETRY_PROMPT.format(sentences=numbered, passages=shown),
                    SENTENCES_SCHEMA,
                ),
                labels,
            )
        except FormatMismatch:
            retried = []
    # A retry entry counts only when it repeats the sentence it lines up with.
    chunk_ids = {
        i: retried[n][1]
        for n, i in enumerate(uncited)
        if n < len(retried) and retried[n][0] == first[i][0]
    }
    out = []
    for i, (text, ids) in enumerate(first):
        ids = ids or chunk_ids.get(i, ())
        if ids:
            out.append(DraftSentence(text, ids))
    return out
