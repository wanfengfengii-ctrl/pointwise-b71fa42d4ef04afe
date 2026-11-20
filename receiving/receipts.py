from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True, slots=True)
class Receipt:
    batch_id: str
    table_id: str
    content_id: str
    receipt_id: str


def initialize_receipts(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS accepted_batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            table_id TEXT NOT NULL,
            content_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE
        )
    ''')


def find_receipt(db: sqlite3.Connection, batch_id: str) -> Receipt | None:
    row = db.execute('''
        SELECT batch_id, table_id, content_id, receipt_id FROM accepted_batches WHERE batch_id = ?
    ''', (batch_id,)).fetchone()
    return None if row is None else Receipt(*row)


def insert_receipt(db: sqlite3.Connection, receipt: Receipt) -> None:
    db.execute('''
        INSERT INTO accepted_batches (batch_id, table_id, content_id, receipt_id)
        VALUES (?, ?, ?, ?)
    ''', (receipt.batch_id, receipt.table_id, receipt.content_id, receipt.receipt_id))
