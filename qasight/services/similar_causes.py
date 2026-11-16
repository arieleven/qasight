"""Suggest a failure's cause from the causes the team already marked on similar failures.

Marking a cause (:mod:`qasight.services.triage`) labels later failures with the
*same* message, numbers and ids aside. This goes one step further: a failure
nobody marked yet gets the cause of the marked failures whose messages are most
like it, worded differently or not. Every marked cause is an example, so the
suggestions get better as the team marks more; nothing is trained or stored, and
no search server is needed.

How it decides, so a person can check it:

- Each message is cut into words (``message_signature`` first, so numbers, hex ids
  and quoted values don't count), word pairs and four-letter word pieces, weighted by how rare they are
  among the marked messages (TF-IDF), and compared by cosine similarity.
- The :data:`TOP_K` most similar marked messages at :data:`MIN_SIMILARITY` or more
  vote, each by its similarity; one from the same cycle counts in full, one from
  another cycle :data:`OTHER_CYCLE_WEIGHT`.
- The winning cause is suggested when its share of the votes times the best
  similarity reaches :data:`MIN_CONFIDENCE`; the suggestion carries that confidence
  and the marked message it is most like, which the UI shows next to it.

:func:`evaluate` replays the marked causes one at a time against the others
(leave-one-out) and reports how often a suggestion would have been right
(precision) and how often there would have been one (coverage):
``GET /api/v1/triage/accuracy`` and ``qasight triage accuracy``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from qasight.services.run_failures import message_signature

MIN_SIMILARITY: Final[float] = 0.3
MIN_CONFIDENCE: Final[float] = 0.3
TOP_K: Final[int] = 5
OTHER_CYCLE_WEIGHT: Final[float] = 0.8
MAX_CHARS: Final[int] = 2000
_WORD = re.compile(r"[a-z_][a-z0-9_]+")


@dataclass(frozen=True)
class Example:
    """A marked cause: its cycle, cause and the message it was marked on."""

    cycle: str
    cause: str
    message: str
    marked_by: str | None = None


@dataclass(frozen=True)
class Suggestion:
    cause: str
    confidence: float
    # The marked message it is most like, and where that was marked.
    example_message: str
    example_cycle: str
    similarity: float


def _terms(message: str) -> Counter[str]:
    words = _WORD.findall(message_signature(message[:MAX_CHARS]))
    terms: Counter[str] = Counter(words)
    terms.update(f"{a} {b}" for a, b in zip(words, words[1:], strict=False))
    # Word pieces, so "connect", "connection" and "connecting" still meet.
    terms.update(f"#{w[i : i + 4]}" for w in words if len(w) > 4 for i in range(len(w) - 3))
    return terms


@dataclass
class CauseModel:
    """The marked causes, indexed for similarity search."""

    examples: Sequence[Example]
    _vectors: list[dict[str, float]] = field(default_factory=list, init=False)
    _idf: dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        bags = [_terms(e.message) for e in self.examples]
        df: Counter[str] = Counter()
        for bag in bags:
            df.update(bag.keys())
        n = max(len(bags), 1)
        self._idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
        self._vectors = [self._vector(bag) for bag in bags]

    def _vector(self, bag: Counter[str]) -> dict[str, float]:
        # Terms no example has get the highest weight: rare, so they make a message unlike the others.
        top = max(self._idf.values(), default=1.0)
        vec = {t: (1 + math.log(c)) * self._idf.get(t, top) for t, c in bag.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def suggest(self, cycle: str, message: str, *, skip: int | None = None) -> Suggestion | None:
        """The cause the most similar marked failures point to, or None when none is close enough."""
        if not message or not self.examples:
            return None
        query = self._vector(_terms(message))
        if not query:
            return None
        scored: list[tuple[float, int]] = []
        for index, vec in enumerate(self._vectors):
            if index == skip:
                continue
            small, large = (query, vec) if len(query) < len(vec) else (vec, query)
            sim = sum(w * large.get(t, 0.0) for t, w in small.items())
            if sim >= MIN_SIMILARITY:
                scored.append((sim, index))
        if not scored:
            return None
        scored.sort(reverse=True)
        votes: dict[str, float] = {}
        for sim, index in scored[:TOP_K]:
            example = self.examples[index]
            weight = 1.0 if example.cycle == cycle else OTHER_CYCLE_WEIGHT
            votes[example.cause] = votes.get(example.cause, 0.0) + sim * weight
        cause, weight = max(votes.items(), key=lambda kv: kv[1])
        best_sim, best_index = next((s, i) for s, i in scored if self.examples[i].cause == cause)
        confidence = round(weight / sum(votes.values()) * best_sim, 3)
        if confidence < MIN_CONFIDENCE:
            return None
        best = self.examples[best_index]
        return Suggestion(
            cause=cause,
            confidence=confidence,
            example_message=best.message[:500],
            example_cycle=best.cycle,
            similarity=round(best_sim, 3),
        )


@dataclass(frozen=True)
class Accuracy:
    examples: int
    suggested: int
    correct: int
    # cause -> (suggested, correct)
    by_cause: dict[str, tuple[int, int]]

    @property
    def precision(self) -> float | None:
        return round(self.correct / self.suggested, 3) if self.suggested else None

    @property
    def coverage(self) -> float | None:
        return round(self.suggested / self.examples, 3) if self.examples else None


def evaluate(examples: Sequence[Example]) -> Accuracy:
    """Leave-one-out: suggest each marked cause from all the others and count the hits."""
    model = CauseModel(examples)
    suggested = correct = 0
    by_cause: dict[str, list[int]] = {}
    for index, example in enumerate(examples):
        guess = model.suggest(example.cycle, example.message, skip=index)
        if guess is None:
            continue
        suggested += 1
        hit = guess.cause == example.cause
        correct += hit
        row = by_cause.setdefault(guess.cause, [0, 0])
        row[0] += 1
        row[1] += int(hit)
    return Accuracy(
        examples=len(examples),
        suggested=suggested,
        correct=correct,
        by_cause={k: (v[0], v[1]) for k, v in sorted(by_cause.items())},
    )


def examples_from_store(cycle: str | None = None) -> list[Example]:
    """The marked causes as examples (every cycle's, unless *cycle* is given)."""
    from qasight.services.triage import TriageStore

    return [
        Example(cycle=t.cycle, cause=t.cause, message=t.example_message, marked_by=t.marked_by)
        for t in TriageStore().list(cycle)
        if t.example_message
    ]


def suggest_many(cycle: str, messages: Iterable[str]) -> dict[str, Suggestion]:
    """Suggestions for several messages of one cycle, built on one read of the marked causes."""
    wanted = [m for m in dict.fromkeys(messages) if m]
    if not wanted:
        return {}
    examples = examples_from_store()
    if not examples:
        return {}
    model = CauseModel(examples)
    out: dict[str, Suggestion] = {}
    for message in wanted:
        found = model.suggest(cycle, message)
        if found is not None:
            out[message] = found
    return out
