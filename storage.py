from contextlib import contextmanager
from decimal import Decimal
import sqlite3


def integer_text(value: int) -> str:
    """Represent a business integer without the binary or string digit limits."""
    return str(Decimal(value))


def restore_integer(value: str) -> int:
    """Read a decimal integer from storage or an integer token from JSON."""
    return int(Decimal(value))


@contextmanager
def write_transaction(db: sqlite3.Connection):
    if db.in_transaction:
        raise ValueError('入口需要没有未提交事务的连接')
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _open_database(path, initializers) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with write_transaction(db):
            for initialize in initializers:
                initialize(db)
    except BaseException:
        db.close()
        raise
    return db


def open_device(path) -> sqlite3.Connection:
    from device.active_table import initialize_active_slot
    from device.batches import initialize_batches
    from device.issue import initialize_issues
    from device.pages import initialize_pages
    from exchange.export import initialize_exports
    from tables.catalog import initialize_catalog

    return _open_database(path, (
        initialize_catalog, initialize_active_slot, initialize_batches,
        initialize_pages, initialize_issues, initialize_exports,
    ))


def open_receiving(path) -> sqlite3.Connection:
    from receiving.receipts import initialize_receipts
    from receiving.rows import initialize_received_rows
    from reporting.snapshots import initialize_reports

    return _open_database(path, (
        initialize_receipts, initialize_received_rows, initialize_reports,
    ))
