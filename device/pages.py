from dataclasses import dataclass
from itertools import groupby
import sqlite3

from device.page_body import SourceRow
from storage import integer_text, restore_integer


@dataclass(frozen=True, slots=True)
class SourcePage:
    sequence: int
    page_id: str
    rows: tuple[SourceRow, ...]


def initialize_pages(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS source_pages (
            batch_id TEXT NOT NULL REFERENCES device_batches(batch_id),
            page_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            PRIMARY KEY (batch_id, page_id),
            UNIQUE (batch_id, sequence)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS source_rows (
            batch_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            line_no INTEGER NOT NULL CHECK (line_no > 0),
            code TEXT NOT NULL,
            milliseconds TEXT NOT NULL,
            PRIMARY KEY (batch_id, page_id, line_no),
            FOREIGN KEY (batch_id, page_id) REFERENCES source_pages(batch_id, page_id)
        )
    ''')


def append_source_row(
    db: sqlite3.Connection, batch_id: str, page_id: str, code: int, milliseconds: int,
) -> int:
    """Allocate positions for a validated observation inside its write transaction."""
    page_key = (batch_id, page_id)
    if db.execute('SELECT 1 FROM source_pages WHERE batch_id = ? AND page_id = ?', page_key).fetchone() is None:
        db.execute('''
            INSERT INTO source_pages (batch_id, page_id, sequence)
            SELECT ?, ?, coalesce(max(sequence), 0) + 1 FROM source_pages WHERE batch_id = ?
        ''', (batch_id, page_id, batch_id))
    position, = db.execute('''
        SELECT coalesce(max(line_no), 0) + 1 FROM source_rows
        WHERE batch_id = ? AND page_id = ?
    ''', page_key).fetchone()
    db.execute('''
        INSERT INTO source_rows (batch_id, page_id, line_no, code, milliseconds)
        VALUES (?, ?, ?, ?, ?)
    ''', (batch_id, page_id, position, integer_text(code), integer_text(milliseconds)))
    return position


def load_source_pages(db: sqlite3.Connection, batch_id: str) -> tuple[SourcePage, ...]:
    rows = db.execute('''
        SELECT p.sequence, p.page_id, r.line_no, r.code, r.milliseconds
        FROM source_pages AS p JOIN source_rows AS r USING (batch_id, page_id)
        WHERE p.batch_id = ? ORDER BY p.sequence, r.line_no
    ''', (batch_id,))
    pages = []
    for (sequence, page_id), observations in groupby(rows, key=lambda row: row[:2]):
        source_rows = tuple(
            SourceRow(line_no, restore_integer(code), restore_integer(milliseconds))
            for _, _, line_no, code, milliseconds in observations
        )
        pages.append(SourcePage(sequence, page_id, source_rows))
    return tuple(pages)
