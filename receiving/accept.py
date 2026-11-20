import sqlite3

from exchange.decode import decode_exchange
from receiving.receipts import Receipt, find_receipt, insert_receipt
from receiving.reconcile import translate_batch
from receiving.rows import insert_accepted_rows
from storage import write_transaction


def receive_batch(db: sqlite3.Connection, payload: bytes) -> Receipt:
    exchange = decode_exchange(payload)
    rows = translate_batch(exchange)
    manifest = exchange.manifest
    with write_transaction(db):
        existing = find_receipt(db, manifest.batch_id)
        if existing is not None:
            if existing.content_id != exchange.content_id:
                raise ValueError('该批已实收另一固定整件')
            return existing
        receipt = Receipt(manifest.batch_id, manifest.table_id, exchange.content_id,
                          'receipt-' + exchange.content_id)
        insert_receipt(db, receipt)
        insert_accepted_rows(db, rows)
    return receipt
