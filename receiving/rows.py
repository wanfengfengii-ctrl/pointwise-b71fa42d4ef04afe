from dataclasses import dataclass
import sqlite3

from storage import integer_text, restore_integer


@dataclass(frozen=True, slots=True)
class AcceptedRow:
    batch_id: str
    table_id: str
    sequence: int
    page_id: str
    line_no: int
    code: int
    segment: str
    milliseconds: int


def initialize_received_rows(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS accepted_rows (
            batch_id TEXT NOT NULL REFERENCES accepted_batches(batch_id),
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            line_no INTEGER NOT NULL CHECK (line_no > 0),
            code TEXT NOT NULL,
            segment TEXT NOT NULL,
            milliseconds TEXT NOT NULL,
            PRIMARY KEY (batch_id, sequence, line_no),
            UNIQUE (batch_id, page_id, line_no)
        )
    ''')


def insert_accepted_rows(db: sqlite3.Connection, rows: tuple[AcceptedRow, ...]) -> None:
    db.executemany('''
        INSERT INTO accepted_rows (batch_id, sequence, page_id, line_no, code, segment, milliseconds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ((row.batch_id, row.sequence, row.page_id, row.line_no, integer_text(row.code),
           row.segment, integer_text(row.milliseconds)) for row in rows))


def load_accepted_rows(db: sqlite3.Connection, batch_id: str) -> tuple[AcceptedRow, ...]:
    records = db.execute('''
        SELECT b.batch_id, b.table_id, r.sequence, r.page_id, r.line_no,
               r.code, r.segment, r.milliseconds
        FROM accepted_rows AS r JOIN accepted_batches AS b USING (batch_id)
        WHERE r.batch_id = ? ORDER BY r.sequence, r.line_no
    ''', (batch_id,))
    return tuple(
        AcceptedRow(batch, table, sequence, page, line, restore_integer(code), segment,
                    restore_integer(milliseconds))
        for batch, table, sequence, page, line, code, segment, milliseconds in records
    )
