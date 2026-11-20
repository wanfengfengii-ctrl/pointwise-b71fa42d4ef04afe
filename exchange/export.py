import sqlite3

from device.batches import load_batch
from device.issue import load_issued_pages
from exchange.encode import encode_exchange
from exchange.manifest import build_manifest
from storage import write_transaction
from tables.catalog import load_table
from tables.validate import require_text


def initialize_exports(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS exported_batches (
            batch_id TEXT PRIMARY KEY NOT NULL REFERENCES device_batches(batch_id),
            payload BLOB NOT NULL
        )
    ''')


def export_batch(db: sqlite3.Connection, batch_id: str) -> bytes:
    require_text(batch_id, '批号')
    with write_transaction(db):
        batch = load_batch(db, batch_id)
        if batch is None or batch.directory is None:
            raise ValueError('只可导出已封定的批')
        table = load_table(db, batch.table_id)
        if table is None:
            raise ValueError('封定批缺少原码表')
        pages = load_issued_pages(db, batch_id)
        manifest = build_manifest(batch, table, pages)
        payload = encode_exchange(manifest, table, pages)
        existing = db.execute(
            'SELECT payload FROM exported_batches WHERE batch_id = ?', (batch_id,)
        ).fetchone()
        if existing is not None:
            if existing[0] != payload:
                raise ValueError('发件副本与固定来源不符')
            return existing[0]
        db.execute('INSERT INTO exported_batches (batch_id, payload) VALUES (?, ?)', (batch_id, payload))
    return payload
