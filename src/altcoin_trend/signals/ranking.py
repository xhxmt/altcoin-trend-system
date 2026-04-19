from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping


def aggregate_rank_rows_by_symbol(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_rows[str(row["symbol"])].append(dict(row))

    aggregated_rows: list[dict[str, Any]] = []
    for symbol, symbol_rows in grouped_rows.items():
        representative = max(symbol_rows, key=lambda row: row["final_score"])
        exchange = representative.get("exchange") or "unknown"
        average_score = round(sum(row["final_score"] for row in symbol_rows) / len(symbol_rows), 4)
        aggregated_row = dict(representative)
        aggregated_row["symbol"] = symbol
        aggregated_row["exchange"] = exchange
        aggregated_row["exchange_count"] = len(symbol_rows)
        aggregated_row["average_score"] = average_score
        aggregated_rows.append(aggregated_row)

    aggregated_rows.sort(key=lambda row: row["final_score"], reverse=True)
    for index, row in enumerate(aggregated_rows, start=1):
        row["rank"] = index
    return aggregated_rows


def rank_scores(rows: list[Mapping[str, Any]], rank_scope: str) -> list[dict[str, Any]]:
    ranked_rows = [dict(row) for row in rows]
    ranked_rows.sort(key=lambda row: row["final_score"], reverse=True)

    for index, row in enumerate(ranked_rows, start=1):
        row["rank_scope"] = rank_scope
        row["rank"] = index

    return ranked_rows
