from __future__ import annotations

from typing import Any, Mapping


def rank_scores(rows: list[Mapping[str, Any]], rank_scope: str) -> list[dict[str, Any]]:
    ranked_rows = [dict(row) for row in rows]
    ranked_rows.sort(key=lambda row: row["final_score"], reverse=True)

    for index, row in enumerate(ranked_rows, start=1):
        row["rank_scope"] = rank_scope
        row["rank"] = index

    return ranked_rows
