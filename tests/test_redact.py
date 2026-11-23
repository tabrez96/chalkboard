from chalkboard.ai.redact import RedactingClient, Redactor, placeholder


def redactor():
    return Redactor(["Leo Martins", "Mia Chen"], "Aamina")


def test_full_name_replaced_case_insensitive():
    assert redactor().redact("leo MARTINS asked") == "Student A asked"


def test_first_and_last_name_parts_replaced():
    assert redactor().redact("Leo said") == "Student A said"
    assert redactor().redact("Martins said") == "Student A said"


def test_whole_word_only():
    assert redactor().redact("Leonard and Leopold") == "Leonard and Leopold"


def test_whitespace_and_punctuation():
    assert redactor().redact("Leo\nMartins's book") == "Student A's book"
    assert redactor().redact("(Mia) and LEO  martins") == "(Student B) and Student A"


def test_shared_first_name():
    r = Redactor(["Leo Martins", "Leo Chen"])
    assert r.redact("Leo") == "Student"
    assert r.redact("Leo Martins and Leo Chen") == "Student A and Student B"
    assert r.redact("Chen") == "Student B"


def test_teacher_name_replaced():
    assert redactor().redact("Aamina") == "Teacher"


def test_teacher_name_wins_over_student_with_same_name():
    assert Redactor(["Aamina"], "Aamina").redact("Aamina") == "Teacher"


def test_short_parts_not_redacted_alone():
    assert Redactor(["Li A"]).redact("A day with Li") == "A day with Student A"


def test_restore_round_trip():
    text = "Leo Martins helped Mia Chen, said Aamina."
    r = redactor()
    assert r.restore(r.redact(text)) == text


def test_restore_prefers_longest_placeholder():
    r = Redactor([f"Pupil{i} Name" for i in range(27)])
    assert r.restore("Student 27 and Student B") == "Pupil26 Name and Pupil1 Name"


def test_placeholder_after_z():
    assert placeholder(0) == "Student A"
    assert placeholder(25) == "Student Z"
    assert placeholder(26) == "Student 27"


def test_no_names_is_identity():
    r = Redactor([])
    assert r.redact("Leo Martins") == "Leo Martins"
    assert r.restore("Student A") == "Student A"


class Recorder:
    def __init__(self):
        self.seen = {}

    def generate(self, prompt, schema):
        self.seen["generate"] = prompt
        return {"ok": True}

    def chat(self, messages):
        self.seen["chat"] = messages
        return "Student A did well"

    def embed(self, texts):
        self.seen["embed"] = texts
        return [[0.0]]

    def health(self):
        return "health"

    def close(self):
        self.seen["closed"] = True


def test_redacting_client_redacts_every_method():
    inner = Recorder()
    client = RedactingClient(inner, redactor())
    assert client.generate("Plan for Leo Martins", {}) == {"ok": True}
    reply = client.chat(
        [
            {"role": "system", "content": "Aamina teaches"},
            {"role": "user", "content": "How is mia?"},
        ]
    )
    client.embed(["Chen wrote", "plain"])
    assert client.health() == "health"
    client.close()
    assert inner.seen["generate"] == "Plan for Student A"
    assert [m["content"] for m in inner.seen["chat"]] == [
        "Teacher teaches",
        "How is Student B?",
    ]
    assert inner.seen["chat"][0]["role"] == "system"
    assert reply == "Leo Martins did well"
    assert inner.seen["embed"] == ["Student B wrote", "plain"]
    assert inner.seen["closed"]


def test_spellings_that_casefold_alike_stay_separate():
    redactor = Redactor(["Weiß", "Weiss"])
    assert redactor.redact("Weiß and Weiss") == "Student A and Student B"


def test_regex_match_without_exact_spelling_does_not_raise():
    # IGNORECASE matches "ipek" against "İpek", but the casefolded forms differ.
    assert Redactor(["İpek"]).redact("ipek") == "Student A"
    assert Redactor(["Ipek"]).redact("İpek") == "Student A"
