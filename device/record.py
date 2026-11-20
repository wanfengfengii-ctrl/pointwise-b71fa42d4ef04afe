import sqlite3

from device.batches import load_batch
from device.page_body import validate_measurement
from device.pages import append_source_row
from storage import write_transaction
from tables.catalog import load_table
from tables.resolve import resolve_code
from tables.validate import require_text


def append_row(
    db: sqlite3.Connection, batch_id: str, page_id: str, code: int, milliseconds: int,
) -> int:
    require_text(batch_id, '批号')
    require_text(page_id, '页号')
    with write_transaction(db):
        batch = load_batch(db, batch_id)
        if batch is None or batch.directory is not None:
            raise ValueError('只可向活动批追加原行')
        validate_measurement(code, milliseconds)
        table = load_table(db, batch.table_id)
        if table is None:
            raise ValueError('活动批缺少固定码表')
        resolve_code(table, code)
        position = append_source_row(db, batch_id, page_id, code, milliseconds)
    return position
