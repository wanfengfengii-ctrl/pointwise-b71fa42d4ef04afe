import sqlite3

from device.batches import DeviceBatch, SealedPage, load_batch, store_sealed_directory
from device.issue import issue_pages
from exchange.integrity import digest_bytes
from storage import write_transaction
from tables.validate import require_text


def seal_batch(db: sqlite3.Connection, batch_id: str) -> DeviceBatch:
    require_text(batch_id, '批号')
    with write_transaction(db):
        batch = load_batch(db, batch_id)
        if batch is None:
            raise ValueError('未知设备批')
        if batch.directory is not None:
            return batch
        issued = issue_pages(db, batch)
        directory = tuple(
            SealedPage(page.sequence, page.page_id, len(page.body), digest_bytes(page.body))
            for page in issued
        )
        store_sealed_directory(db, batch, directory)
    return DeviceBatch(batch_id, batch.table_id, directory)
