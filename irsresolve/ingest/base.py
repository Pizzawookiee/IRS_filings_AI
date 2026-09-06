"""Document ingestion (impl-plan §Ingestion, architecture §4.4).

Parsers turn an uploaded document into `ProposedFacts` — path → {value, source, ref} with
attested=False. They NEVER write to Facts directly. `merge()` is the confirm step: the values the
taxpayer accepted become attested=True at their canonical paths; unconfirmed proposals are dropped.
This keeps every evaluated fact taxpayer-attested and tolerant of OCR error.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Protocol

from ..core.facts import Facts, ProposedFacts


class DocumentParser(Protocol):
    def parse(self, path: str | Path) -> ProposedFacts: ...


def _set_path(tree: dict, dotted: str, value) -> None:
    node = tree
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def merge(base_facts: dict, proposed: ProposedFacts, accepted_paths: set[str]) -> Facts:
    """Apply accepted proposals onto a base facts dict as attested values, then validate.

    `base_facts` holds the taxpayer's own answers; `accepted_paths` are the proposal paths the
    taxpayer confirmed on the review screen. Edited values should already be written into
    `base_facts` by the caller (they become source="user"). Unaccepted proposals are dropped.
    """
    merged = copy.deepcopy(base_facts)
    for path, prop in proposed.values.items():
        if path in accepted_paths:
            _set_path(merged, path, {
                "value": prop["value"],
                "provenance": {"source": prop["source"], "ref": prop.get("ref"), "attested": True},
            })
    return Facts.model_validate(merged)
