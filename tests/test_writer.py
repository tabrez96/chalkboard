from chalkboard.ai.base import FormatMismatch
from chalkboard.ai.check import NOT_SUPPORTED, UNCHECKED, check_sentences
from chalkboard.ai.fake import FakeLLM
from chalkboard.ai.retrieve import Passage
from chalkboard.ai.write import DraftSentence, parse_sentences, write_sentences

PASSAGES = [
    Passage("P1", 11, 1, "Light bends.", None, None),
    Passage("P2", 22, 2, "Cells divide.", 3, 4),
]


def answer(*sentences):
    return {"sentences": [{"text": t, "passages": p} for t, p in sentences]}


class RaisingLLM(FakeLLM):
    def generate(self, prompt, schema):
        raise FormatMismatch("bad")


def test_labels_map_to_chunk_ids():
    result = parse_sentences(
        answer(("A  fact.", ["p2", " P1 ", "P9", "P2"])), {"P1": 11, "P2": 22}
    )
    assert result == [("A fact.", (22, 11))]


def test_parse_skips_malformed_items():
    raw = {
        "sentences": [
            "x",
            {"text": 3},
            {"text": "  "},
            {"text": "Ok.", "passages": "P1"},
            {"text": "Fine.", "passages": [1, "P1"]},
        ]
    }
    assert parse_sentences(raw, {"P1": 11}) == [("Ok.", ()), ("Fine.", (11,))]


def test_uncited_sentence_is_retried_once_then_kept():
    llm = FakeLLM([answer(("A.", ["P1"]), ("B.", [])), answer(("B.", ["P2"]))])
    result = write_sentences(llm, "Write", PASSAGES)
    assert [(s.text, s.chunk_ids) for s in result] == [("A.", (11,)), ("B.", (22,))]
    assert len(llm.calls) == 2


def test_uncited_sentence_is_dropped_after_retry():
    llm = FakeLLM(
        [answer(("A.", ["P1"]), ("B.", []), ("C.", ["P2"])), answer(("B.", []))]
    )
    result = write_sentences(llm, "Write", PASSAGES)
    assert [s.text for s in result] == ["A.", "C."]
    assert len(llm.calls) == 2


def test_retry_with_missing_or_reordered_sentence_drops_it():
    first = answer(("A.", []), ("B.", []))
    # Only B comes back: its citations must not land on A.
    llm = FakeLLM([first, answer(("B.", ["P2"]))])
    assert write_sentences(llm, "Write", PASSAGES) == []
    # Reordered: neither entry lines up with its own sentence.
    llm = FakeLLM([first, answer(("B.", ["P2"]), ("A.", ["P1"]))])
    assert write_sentences(llm, "Write", PASSAGES) == []


def test_missing_retry_position_and_retry_format_error_drop_the_sentence():
    llm = FakeLLM([answer(("A.", []), ("B.", [])), answer(("A.", ["P1"]))])
    assert [s.text for s in write_sentences(llm, "Write", PASSAGES)] == ["A."]

    class FailsOnRetry(FakeLLM):
        def generate(self, prompt, schema):
            if self.calls:
                raise FormatMismatch("bad")
            return super().generate(prompt, schema)

    llm = FailsOnRetry([answer(("A.", []))])
    assert write_sentences(llm, "Write", PASSAGES) == []


def test_no_retry_when_everything_is_cited():
    llm = FakeLLM([answer(("A.", ["P1"]))])
    assert len(write_sentences(llm, "Write", PASSAGES)) == 1
    assert len(llm.calls) == 1


def test_no_passages_means_no_call():
    llm = FakeLLM()
    assert write_sentences(llm, "Write", []) == []
    assert llm.calls == []


def test_garbage_answer_gives_no_sentences():
    assert write_sentences(FakeLLM([{}]), "Write", PASSAGES) == []
    assert write_sentences(FakeLLM([{"sentences": "x"}]), "Write", PASSAGES) == []
    assert write_sentences(FakeLLM(["nope"]), "Write", PASSAGES) == []


def test_checker_flags_unsupported_sentence_with_reason():
    llm = FakeLLM(
        [
            {"supported": True, "reason": "Says so"},
            {"supported": False, "reason": "The passage is about mitosis"},
        ]
    )
    [checked] = check_sentences(llm, [DraftSentence("X.", (11, 22))], PASSAGES)
    assert checked.check_flag
    assert checked.check_reason == "Source 2: The passage is about mitosis"
    assert len(llm.calls) == 2


def test_checker_keeps_supported_sentence_unflagged():
    llm = FakeLLM([{"supported": True, "reason": "ok"}] * 2)
    [checked] = check_sentences(llm, [DraftSentence("X.", (11, 22))], PASSAGES)
    assert not checked.check_flag
    assert checked.check_reason is None


def test_checker_treats_non_boolean_and_bad_format_as_unsupported():
    llm = FakeLLM([{"supported": "true", "reason": ""}])
    [checked] = check_sentences(llm, [DraftSentence("X.", (11,))], PASSAGES)
    assert checked.check_flag
    assert checked.check_reason == f"Source 1: {NOT_SUPPORTED}"
    [checked] = check_sentences(RaisingLLM(), [DraftSentence("X.", (11,))], PASSAGES)
    assert checked.check_reason == f"Source 1: {UNCHECKED}"


def test_checker_skips_unknown_chunk_and_cuts_long_reasons():
    llm = FakeLLM([{"supported": False, "reason": "x" * 500}])
    [checked] = check_sentences(llm, [DraftSentence("X.", (99, 11))], PASSAGES)
    assert checked.check_reason == "Source 1: " + "x" * 300
