import sqlite3

from storage import write_transaction
from tables.catalog import CodeTable, is_retired_table, load_table
from tables.validate import require_text


def initialize_active_slot(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS active_slot (
            slot INTEGER PRIMARY KEY CHECK (slot = 1),
            table_id TEXT NOT NULL REFERENCES code_tables(table_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS frozen_sections (
            table_id TEXT PRIMARY KEY NOT NULL REFERENCES code_tables(table_id),
            seal TEXT NOT NULL UNIQUE,
            control BLOB NOT NULL,
            receipt_id TEXT
        )
    ''')


def read_active_table(db: sqlite3.Connection) -> CodeTable | None:
    row = db.execute('SELECT table_id FROM active_slot WHERE slot = 1').fetchone()
    return None if row is None else load_table(db, row[0])


def active_slot_id(db: sqlite3.Connection) -> str | None:
    row = db.execute('SELECT table_id FROM active_slot WHERE slot = 1').fetchone()
    return None if row is None else row[0]


def frozen_section(db: sqlite3.Connection, table_id: str):
    return db.execute('''
        SELECT table_id, seal, control, receipt_id FROM frozen_sections WHERE table_id = ?
    ''', (table_id,)).fetchone()


def active_slot_frozen(db: sqlite3.Connection) -> bool:
    table_id = active_slot_id(db)
    return table_id is not None and frozen_section(db, table_id) is not None


def select_table_for_batch(db: sqlite3.Connection, table_id: str) -> CodeTable:
    """Select the slot within the caller's opening transaction."""
    require_text(table_id, '表号')
    if not db.in_transaction:
        raise ValueError('选表须在开批事务中进行')
    if active_slot_frozen(db):
        raise ValueError('当前代已冻结，换表前不可再开批发页')
    if db.execute('SELECT 1 FROM device_batches WHERE sealed = 0').fetchone() is not None:
        raise ValueError('上一活动批须先封定并签发')
    if is_retired_table(db, table_id):
        raise ValueError('已退出的表号不可再开批')
    table = load_table(db, table_id)
    if table is None:
        raise ValueError('所选码表未登记')
    db.execute('''
        INSERT INTO active_slot (slot, table_id) VALUES (1, ?)
        ON CONFLICT(slot) DO UPDATE SET table_id = excluded.table_id
    ''', (table_id,))
    return table


def activate_table(db: sqlite3.Connection, new_table: str, receipt: bytes) -> str:
    """Switch the active slot on a valid takeover receipt and clear the old map."""
    require_text(new_table, '新表号')
    if type(receipt) is not bytes:
        raise ValueError('回执须为 UTF-8 字节')
    from exchange.turnover import parse_receipt

    parsed = parse_receipt(receipt)
    with write_transaction(db):
        current_id = active_slot_id(db)
        if current_id is None:
            raise ValueError('设备尚无活动码表')
        section = frozen_section(db, current_id)
        if section is None:
            raise ValueError('当前代尚未冻结，无截面可接管')
        _, seal, _, consumed_receipt = section
        if parsed.seal != seal:
            raise ValueError('回执未绑定当前冻结截面')
        if parsed.table_id != current_id:
            raise ValueError('回执与截面表号错绑')
        if consumed_receipt is not None and consumed_receipt != parsed.receipt_id:
            raise ValueError('该截面已使用另一回执换表')
        if is_retired_table(db, new_table):
            raise ValueError('新表号已退出，不可启用')
        target = load_table(db, new_table)
        if target is None:
            raise ValueError('新表号未登记或已退出')
        if new_table == current_id:
            raise ValueError('新表须为另一张码表')
        # 旧码表映射清除与槽位切换、回执绑定一并提交。
        db.execute('DELETE FROM code_entries WHERE table_id = ?', (current_id,))
        db.execute('INSERT INTO retired_tables (table_id) VALUES (?)', (current_id,))
        db.execute('''
            UPDATE frozen_sections SET receipt_id = ? WHERE table_id = ? AND seal = ?
        ''', (parsed.receipt_id, current_id, seal))
        db.execute('UPDATE active_slot SET table_id = ? WHERE slot = 1', (new_table,))
    return new_table
