from collections.abc import Iterable, Mapping
import sqlite3

from storage import write_transaction
from tables.catalog import CodeTable, insert_table, load_table
from tables.validate import normalize_entries


def register_table(
    db: sqlite3.Connection,
    table_id: str,
    entries: Mapping[int, str] | Iterable[tuple[int, str]],
) -> str:
    table = CodeTable(table_id, normalize_entries(entries))
    with write_transaction(db):
        existing = load_table(db, table.table_id)
        if existing is None:
            insert_table(db, table)
        elif existing.entries != table.entries:
            raise ValueError('同号码表内容已固定')
    return table.table_id
