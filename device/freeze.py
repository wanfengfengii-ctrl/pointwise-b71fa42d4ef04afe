"""Freeze one code-table generation into a standalone turnover control piece."""
import sqlite3

from device.active_table import active_slot_id, frozen_section
from device.batches import load_batch
from exchange.turnover import build_control, parse_control
from storage import write_transaction
from tables.catalog import load_table
from tables.validate import require_text


def _generation_batches(db: sqlite3.Connection, table_id: str):
    rows = db.execute(
        'SELECT batch_id FROM device_batches WHERE table_id = ? ORDER BY batch_id',
        (table_id,),
    )
    return [load_batch(db, batch_id) for (batch_id,) in rows]


def prepare_turnover(db: sqlite3.Connection, table_id: str) -> bytes:
    require_text(table_id, '表号')
    with write_transaction(db):
        if active_slot_id(db) != table_id:
            raise ValueError('只能为当前活动槽的码表办理换表交班')
        existing = frozen_section(db, table_id)
        if existing is not None:
            # 待换表期间重取同一截面得到相同控制件。
            return existing[2]
        table = load_table(db, table_id)
        if table is None:
            raise ValueError('活动码表未登记或已退出')
        if db.execute('SELECT 1 FROM device_batches WHERE sealed = 0').fetchone() is not None:
            raise ValueError('须先封定本地活动批才可冻结截面')
        batches = _generation_batches(db, table_id)
        if not batches:
            raise ValueError('该代尚无已发批，无可冻结截面')
        if any(batch.directory is None for batch in batches):
            raise ValueError('该代存在未封定批，截面不全')
        control = build_control(table, tuple(batches))
        seal = parse_control(control).seal
        db.execute('''
            INSERT INTO frozen_sections (table_id, seal, control) VALUES (?, ?, ?)
        ''', (table_id, seal, control))
    return control
