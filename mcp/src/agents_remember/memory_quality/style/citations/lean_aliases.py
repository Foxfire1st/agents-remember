"""Read Lean alias binders through their target identifier."""

from dataclasses import dataclass, field

from agents_remember.memory_quality.style.citations.lean_names import LeanName, read_name


@dataclass
class AliasHeader:
    """Retain the alias command's name slots across physical source lines."""

    _remaining: tuple[str, ...] | None = None
    _names: list[LeanName] = field(default_factory=list)

    def read(self, text: str) -> tuple[LeanName, ...] | None:
        """Read one masked line, returning names once the target is reached."""
        text = text.lstrip()
        if not text:
            return None
        if self._remaining is None:
            self._remaining = (
                ("⟨", "slot", ",", "slot", "⟩", ":=", "target")
                if text.startswith("⟨")
                else ("name", ":=", "target")
            )
        while self._remaining and text:
            token = self._remaining[0]
            if token in {"name", "slot", "target"}:
                name = read_name(text)
                if name is None:
                    return ()
                if token == "name" or (token == "slot" and name.text != "_"):
                    self._names.append(name)
                end = name.end
            elif text.startswith(token):
                end = len(token)
            else:
                return ()
            text = text[end:].lstrip()
            self._remaining = self._remaining[1:]
        return None if self._remaining else tuple(self._names)
