import sqlite3

from tables.catalog import CodeTable, load_table
from tables.validate import require_text


def initialize_active_slot(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS active_slot (
            slot INTEGER PRIMARY KEY CHECK (slot = 1),
            table_id TEXT NOT NULL REFERENCES code_tables(table_id)
        )
    ''')


def read_active_table(db: sqlite3.Connection) -> CodeTable | None:
    row = db.execute('SELECT table_id FROM active_slot WHERE slot = 1').fetchone()
    return None if row is None else load_table(db, row[0])


def select_table_for_batch(db: sqlite3.Connection, table_id: str) -> CodeTable:
    """Select the slot within the caller's opening transaction."""
    require_text(table_id, '表号')
    if not db.in_transaction:
        raise ValueError('选表须在开批事务中进行')
    if db.execute('SELECT 1 FROM device_batches WHERE sealed = 0').fetchone() is not None:
        raise ValueError('上一活动批须先封定并签发')
    table = load_table(db, table_id)
    if table is None:
        raise ValueError('所选码表未登记')
    db.execute('''
        INSERT INTO active_slot (slot, table_id) VALUES (1, ?)
        ON CONFLICT(slot) DO UPDATE SET table_id = excluded.table_id
    ''', (table_id,))
    return table
