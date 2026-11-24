"""What a grooming produces, whichever system made it: stories plus the open
questions and risks raised along the way."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def capped(n: int):
    """A string type whose JSON schema says maxLength n, and which is trimmed
    to n when read. Ollama enforces maxLength while generating, which stops a
    small model repeating itself until it's cut off; Claude's structured
    outputs don't support it (the SDK moves it into the description), so a
    longer reply is trimmed rather than rejected."""
    return Annotated[str, Field(json_schema_extra={"maxLength": n}), AfterValidator(lambda s: s[:n])]


def capped_list(item, n: int):
    """A list type with maxItems n in its JSON schema, trimmed to n when read
    (see `capped`)."""
    return Annotated[list[item], Field(json_schema_extra={"maxItems": n}), AfterValidator(lambda xs: xs[:n])]


Text = capped(400)


class Story(BaseModel):
    title: capped(120)
    user_story: Text = Field(description="As a <role>, I want <goal>, so that <benefit>.")
    acceptance_criteria: capped_list(Text, 8) = Field(description="Specific, testable conditions for done.")
    points: int | None = Field(None, description="Story points on the Fibonacci scale: 1, 2, 3, 5, 8 or 13.")
    dependencies: capped_list(Text, 5) = Field(default_factory=list, description="Other stories or work this needs first.")

    def text(self) -> str:
        lines = [self.title, self.user_story, *self.acceptance_criteria, *self.dependencies]
        return "\n".join(lines)


class Grooming(BaseModel):
    stories: capped_list(Story, 10)
    open_questions: capped_list(Text, 8) = Field(default_factory=list, description="Questions for the requester that change the work.")
    risks: capped_list(Text, 8) = Field(default_factory=list, description="Technical risks, conflicts with existing code, or things that already exist.")

    def items(self, field: str) -> list[str]:
        """The text of each story, open question or risk."""
        if field == "stories":
            return [s.text() for s in self.stories]
        return list(getattr(self, field))

    def total_points(self) -> int:
        return sum(s.points or 0 for s in self.stories)

    def render(self) -> str:
        """Plain text for a judge or a person to read."""
        out = []
        for n, s in enumerate(self.stories, 1):
            out.append(f"Story {n}: {s.title} ({s.points if s.points is not None else '?'} points)\n  {s.user_story}")
            out += [f"  - {c}" for c in s.acceptance_criteria]
            if s.dependencies:
                out.append(f"  Depends on: {'; '.join(s.dependencies)}")
        if self.open_questions:
            out.append("Open questions:\n" + "\n".join(f"  - {q}" for q in self.open_questions))
        if self.risks:
            out.append("Risks:\n" + "\n".join(f"  - {r}" for r in self.risks))
        return "\n".join(out)
