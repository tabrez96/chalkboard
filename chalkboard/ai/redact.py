import re

from chalkboard.ai.base import Health, Vector

TEACHER_PLACEHOLDER = "Teacher"
SHARED_PLACEHOLDER = "Student"  # a name part two students share


def placeholder(index: int) -> str:
    # 0 -> "Student A" … 25 -> "Student Z", 26 -> "Student 27"
    return f"Student {chr(65 + index)}" if index < 26 else f"Student {index + 1}"


def _key(parts: list[str]) -> str:
    # lower(), not casefold(): spellings the regex tells apart (Weiß, Weiss) stay apart.
    return " ".join(parts).lower()


class Redactor:
    """Replaces student and teacher names with placeholders, and back again."""

    def __init__(self, students: list[str], teacher: str | None = None):
        people = [(teacher, TEACHER_PLACEHOLDER)] if teacher else []
        people += [(name, placeholder(i)) for i, name in enumerate(students)]
        self._full_names = {
            placeholder(i): " ".join(name.split()) for i, name in enumerate(students)
        }
        self._teacher = " ".join(teacher.split()) if teacher else None

        full: dict[str, tuple[list[str], str]] = {}
        parts_of: dict[str, tuple[list[str], str]] = {}
        for name, mark in people:
            parts = name.split()
            if not parts:
                continue
            full.setdefault(_key(parts), (parts, mark))
            for part in parts:
                if len(part) < 2:
                    continue
                known = parts_of.get(_key([part]))
                if known is None:
                    parts_of[_key([part])] = ([part], mark)
                elif known[1] != mark:
                    parts_of[_key([part])] = (known[0], SHARED_PLACEHOLDER)

        terms = {**parts_of, **full}  # a full name always wins over a part
        ordered = sorted(
            terms.values(), key=lambda t: len(" ".join(t[0])), reverse=True
        )
        # One capture group per spelling: the group that matched gives the placeholder.
        self._marks = [mark for _, mark in ordered]
        alternatives = [
            "(" + r"\s+".join(re.escape(p) for p in parts) + ")" for parts, _ in ordered
        ]
        self._pattern = (
            re.compile(
                r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE
            )
            if alternatives
            else None
        )

        restores = {mark: name for mark, name in self._full_names.items() if name}
        if self._teacher:
            restores[TEACHER_PLACEHOLDER] = self._teacher
        self._restores = restores
        marks = sorted(restores, key=len, reverse=True)
        self._restore_pattern = (
            re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(m) for m in marks) + r")(?!\w)"
            )
            if marks
            else None
        )

    def redact(self, text: str) -> str:
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self._marks[m.lastindex - 1], text)

    def restore(self, text: str) -> str:
        if self._restore_pattern is None:
            return text
        return self._restore_pattern.sub(lambda m: self._restores[m.group(0)], text)


class RedactingClient:
    """The only way the app reaches a model: names are replaced before every request."""

    def __init__(self, inner, redactor: Redactor):
        self._inner = inner  # the real model client; private on purpose
        self.redactor = redactor

    def generate(self, prompt: str, schema: dict) -> dict:
        return self._inner.generate(self.redactor.redact(prompt), schema)

    def chat(self, messages: list[dict[str, str]]) -> str:
        clean = [{**m, "content": self.redactor.redact(m["content"])} for m in messages]
        return self.redactor.restore(self._inner.chat(clean))

    def embed(self, texts: list[str]) -> list[Vector]:
        return self._inner.embed([self.redactor.redact(t) for t in texts])

    def health(self) -> Health:
        return self._inner.health()  # sends no text

    def close(self) -> None:
        self._inner.close()
