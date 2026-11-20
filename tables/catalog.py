from collections.abc import Mapping
from dataclasses import dataclass, field
import sqlite3
from types import MappingProxyType

from storage import integer_text, restore_integer
from tables.validate import normalize_entries, require_text


@dataclass(frozen=True, slots=True)
class CodeTable:
    table_id: str
    entries: tuple[tuple[int, str], ...]
    by_code: Mapping[int, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        require_text(self.table_id, '表号')
        entries = normalize_entries(self.entries)
        object.__setattr__(self, 'entries', entries)
        object.__setattr__(self, 'by_code', MappingProxyType(dict(entries)))


def initialize_catalog(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS code_tables (
            table_id TEXT PRIMARY KEY NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS code_entries (
            table_id TEXT NOT NULL REFERENCES code_tables(table_id),
            code TEXT NOT NULL,
            segment TEXT NOT NULL,
            PRIMARY KEY (table_id, code)
        )
    ''')


def load_table(db: sqlite3.Connection, table_id: str) -> CodeTable | None:
    if db.execute('SELECT 1 FROM code_tables WHERE table_id = ?', (table_id,)).fetchone() is None:
        return None
    rows = db.execute(
        'SELECT code, segment FROM code_entries WHERE table_id = ?', (table_id,)
    )
    return CodeTable(table_id, tuple((restore_integer(code), segment) for code, segment in rows))


def insert_table(db: sqlite3.Connection, table: CodeTable) -> None:
    db.execute('INSERT INTO code_tables (table_id) VALUES (?)', (table.table_id,))
    db.executemany(
        'INSERT INTO code_entries (table_id, code, segment) VALUES (?, ?, ?)',
        ((table.table_id, integer_text(code), segment) for code, segment in table.entries),
    )
