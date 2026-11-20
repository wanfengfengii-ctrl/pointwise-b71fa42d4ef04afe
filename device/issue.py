from dataclasses import dataclass
import sqlite3

from device.batches import DeviceBatch
from device.page_body import encode_page_body
from device.pages import load_source_pages


@dataclass(frozen=True, slots=True)
class IssuedPage:
    batch_id: str
    table_id: str
    sequence: int
    page_id: str
    body: bytes


def initialize_issues(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS issued_pages (
            batch_id TEXT NOT NULL REFERENCES device_batches(batch_id),
            table_id TEXT NOT NULL REFERENCES code_tables(table_id),
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            body BLOB NOT NULL,
            PRIMARY KEY (batch_id, page_id),
            UNIQUE (batch_id, sequence)
        )
    ''')


def issue_pages(db: sqlite3.Connection, batch: DeviceBatch) -> tuple[IssuedPage, ...]:
    if batch.directory is not None:
        raise ValueError('已封批的原页已签发')
    issued = tuple(
        IssuedPage(batch.batch_id, batch.table_id, page.sequence, page.page_id,
                   encode_page_body(page.rows))
        for page in load_source_pages(db, batch.batch_id)
    )
    db.executemany('''
        INSERT INTO issued_pages (batch_id, table_id, sequence, page_id, body)
        VALUES (?, ?, ?, ?, ?)
    ''', ((page.batch_id, page.table_id, page.sequence, page.page_id, page.body) for page in issued))
    return issued


def load_issued_pages(db: sqlite3.Connection, batch_id: str) -> tuple[IssuedPage, ...]:
    issued = db.execute('''
        SELECT batch_id, table_id, sequence, page_id, body
        FROM issued_pages WHERE batch_id = ? ORDER BY sequence
    ''', (batch_id,))
    return tuple(IssuedPage(*row) for row in issued)
