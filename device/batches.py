from dataclasses import dataclass
import sqlite3

from device.active_table import select_table_for_batch
from storage import write_transaction
from tables.validate import require_text


@dataclass(frozen=True, slots=True)
class SealedPage:
    sequence: int
    page_id: str
    byte_length: int
    body_digest: str


@dataclass(frozen=True, slots=True)
class DeviceBatch:
    batch_id: str
    table_id: str
    directory: tuple[SealedPage, ...] | None = None


def initialize_batches(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            table_id TEXT NOT NULL REFERENCES code_tables(table_id),
            sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1))
        )
    ''')
    db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_batch
        ON device_batches (sealed) WHERE sealed = 0
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS sealed_directory (
            batch_id TEXT NOT NULL REFERENCES device_batches(batch_id),
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            byte_length INTEGER NOT NULL CHECK (byte_length > 0),
            body_digest TEXT NOT NULL,
            PRIMARY KEY (batch_id, sequence),
            UNIQUE (batch_id, page_id)
        )
    ''')


def open_batch(db: sqlite3.Connection, batch_id: str, table_id: str) -> DeviceBatch:
    require_text(batch_id, '批号')
    with write_transaction(db):
        if db.execute('SELECT 1 FROM device_batches WHERE batch_id = ?', (batch_id,)).fetchone():
            raise ValueError('批号已使用')
        table = select_table_for_batch(db, table_id)
        db.execute('INSERT INTO device_batches (batch_id, table_id) VALUES (?, ?)',
                   (batch_id, table.table_id))
    return DeviceBatch(batch_id, table.table_id)


def load_batch(db: sqlite3.Connection, batch_id: str) -> DeviceBatch | None:
    record = db.execute(
        'SELECT table_id, sealed FROM device_batches WHERE batch_id = ?', (batch_id,)
    ).fetchone()
    if record is None:
        return None
    table_id, sealed = record
    if not sealed:
        return DeviceBatch(batch_id, table_id)
    entries = db.execute('''
        SELECT sequence, page_id, byte_length, body_digest
        FROM sealed_directory WHERE batch_id = ? ORDER BY sequence
    ''', (batch_id,))
    return DeviceBatch(batch_id, table_id, tuple(SealedPage(*entry) for entry in entries))


def store_sealed_directory(
    db: sqlite3.Connection, batch: DeviceBatch, directory: tuple[SealedPage, ...],
) -> None:
    db.executemany('''
        INSERT INTO sealed_directory (batch_id, sequence, page_id, byte_length, body_digest)
        VALUES (?, ?, ?, ?, ?)
    ''', ((batch.batch_id, entry.sequence, entry.page_id, entry.byte_length, entry.body_digest)
          for entry in directory))
    updated = db.execute('''
        UPDATE device_batches SET sealed = 1 WHERE batch_id = ? AND sealed = 0
    ''', (batch.batch_id,))
    if updated.rowcount != 1:
        raise ValueError('批不处于可封定状态')
