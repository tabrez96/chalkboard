"""The checker: one call per sentence and cited passage; unsupported sentences are flagged "Check this"."""

import dataclasses

from chalkboard.ai.base import FormatMismatch, LLMClient
from chalkboard.ai.retrieve import Passage
from chalkboard.ai.write import DraftSentence

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["supported", "reason"],
}
CHECK_PROMPT = (
    "Does the passage support the sentence? Answer supported = true only if the passage states it or it follows "
    "directly from it. Give the reason in one short sentence.\n\nSentence: {sentence}\n\nPassage: {passage}"
)
NOT_SUPPORTED = "The cited passage doesn't say this"
UNCHECKED = "The check couldn't read the model's answer"
REASON_MAX = 300


def check_pair(client: LLMClient, sentence: str, passage: str) -> tuple[bool, str]:
    try:
        answer = client.generate(
            CHECK_PROMPT.format(sentence=sentence, passage=passage), CHECK_SCHEMA
        )
    except FormatMismatch:
        return False, UNCHECKED
    if not isinstance(answer, dict):
        return False, UNCHECKED
    supported = answer.get("supported") is True
    reason = answer.get("reason")
    reason = reason.strip()[:REASON_MAX] if isinstance(reason, str) else ""
    if not reason and not supported:
        reason = NOT_SUPPORTED
    return supported, reason


def check_sentences(
    client: LLMClient, sentences: list[DraftSentence], passages: list[Passage]
) -> list[DraftSentence]:
    by_chunk = {p.chunk_id: p for p in passages}
    out = []
    for sentence in sentences:
        reasons = []
        for chunk_id in sentence.chunk_ids:
            passage = by_chunk.get(chunk_id)
            if passage is None:
                continue
            supported, reason = check_pair(client, sentence.text, passage.text)
            if not supported:
                reasons.append(f"Source {passage.source_number}: {reason}")
        if reasons:
            sentence = dataclasses.replace(
                sentence, check_flag=True, check_reason="; ".join(reasons)
            )
        out.append(sentence)
    return out
