"""Shared rules for the two intake routes: V1 whole-piece and V2 split pages."""
import sqlite3

from device.issue import IssuedPage
from device.page_body import decode_page_body
from exchange.encode import encode_exchange
from exchange.manifest import Manifest, PageEntry
from exchange.turnover import TurnoverControl
from receiving.receipts import Receipt, insert_receipt
from receiving.rows import AcceptedRow, insert_accepted_rows
from storage import integer_text
from tables.catalog import CodeTable
from tables.resolve import resolve_code


def find_turnover_batch(db: sqlite3.Connection, batch_id: str):
    return db.execute('''
        SELECT t.seal, t.table_id, t.table_digest
        FROM turnover_batches AS b JOIN turnovers AS t USING (seal)
        WHERE b.batch_id = ?
    ''', (batch_id,)).fetchone()


def find_directory(db: sqlite3.Connection, batch_id: str):
    header = db.execute('''
        SELECT table_id, table_digest FROM batch_directories WHERE batch_id = ?
    ''', (batch_id,)).fetchone()
    if header is None:
        return None
    pages = tuple(db.execute('''
        SELECT sequence, page_id, byte_length, body_digest
        FROM directory_pages WHERE batch_id = ? ORDER BY sequence
    ''', (batch_id,)))
    return header, pages


def directory_signature(db: sqlite3.Connection, batch_id: str):
    directory = find_directory(db, batch_id)
    if directory is None:
        return None
    (table_id, table_digest), pages = directory
    return table_id, table_digest, tuple(pages)


def save_directory(db: sqlite3.Connection, batch_id: str, table_id: str,
                   table_digest: str, pages: tuple) -> None:
    db.execute('''
        INSERT OR IGNORE INTO batch_directories (batch_id, table_id, table_digest)
        VALUES (?, ?, ?)
    ''', (batch_id, table_id, table_digest))
    db.executemany('''
        INSERT OR IGNORE INTO directory_pages (batch_id, sequence, page_id, byte_length, body_digest)
        VALUES (?, ?, ?, ?, ?)
    ''', ((batch_id, sequence, page_id, byte_length, body_digest)
          for sequence, page_id, byte_length, body_digest in pages))


def translate_pending_page(db: sqlite3.Connection, seal: str, table_id: str,
                           page: IssuedPage) -> tuple[AcceptedRow, ...]:
    entries = tuple(
        (int(code), segment)
        for code, segment in db.execute(
            'SELECT code, segment FROM turnover_codes WHERE seal = ? ORDER BY code', (seal,))
    )
    table = CodeTable(table_id, entries)
    source_rows = decode_page_body(page.body)
    return tuple(
        AcceptedRow(page.batch_id, table_id, page.sequence, page.page_id,
                    source_row.line_no, source_row.code,
                    resolve_code(table, source_row.code), source_row.milliseconds)
        for source_row in source_rows
    )


def _encode_split_payload(batch_id: str, table_id: str, table_digest: str,
                          table: CodeTable, pages: tuple[IssuedPage, ...]) -> bytes:
    entries = tuple(
        PageEntry(page.sequence, page.page_id, len(page.body), _digest(page.body))
        for page in sorted(pages, key=lambda item: item.sequence)
    )
    manifest = Manifest(batch_id, table_id, table_digest, entries)
    return encode_exchange(manifest, table, pages)


def _digest(body: bytes) -> str:
    from exchange.integrity import digest_bytes

    return digest_bytes(body)


def complete_split_batch(db: sqlite3.Connection, seal: str, batch_id: str,
                         table_id: str, table_digest: str) -> Receipt:
    """Promote a fully collected turnover batch into a formal accepted batch."""
    stored = db.execute('''
        SELECT sequence, page_id, body, body_digest
        FROM pending_pages WHERE batch_id = ? ORDER BY sequence
    ''', (batch_id,))
    pending = tuple(IssuedPage(batch_id, table_id, sequence, page_id, body)
                    for sequence, page_id, body, _ in stored)
    directory = find_directory(db, batch_id)
    if directory is None:
        raise ValueError('齐套批缺少接管清册')
    if len(pending) != len(directory[1]):
        raise ValueError('原页未齐备，不能齐批')
    entries = tuple(
        (int(code), segment)
        for code, segment in db.execute(
            'SELECT code, segment FROM turnover_codes WHERE seal = ? ORDER BY code', (seal,))
    )
    table = CodeTable(table_id, entries)
    payload = _encode_split_payload(batch_id, table_id, table_digest, table, pending)
    from exchange.integrity import digest_bytes

    content_id = digest_bytes(payload)
    receipt = Receipt(batch_id, table_id, content_id, 'receipt-' + content_id)
    insert_receipt(db, receipt)
    rows = []
    for page in pending:
        rows.extend(translate_pending_page(db, seal, table_id, page))
    insert_accepted_rows(db, tuple(rows))
    db.execute('DELETE FROM pending_pages WHERE batch_id = ?', (batch_id,))
    db.execute('DELETE FROM staged_pages WHERE batch_id = ?', (batch_id,))
    return receipt


def persist_turnover(db: sqlite3.Connection, control: TurnoverControl,
                     control_bytes: bytes, receipt_bytes: bytes) -> None:
    seal = control.seal
    table = control.evidence.table
    db.execute('''
        INSERT INTO turnovers (seal, table_id, table_digest, control, receipt)
        VALUES (?, ?, ?, ?, ?)
    ''', (seal, control.table_id, control.evidence.table_digest,
          control_bytes, receipt_bytes))
    db.executemany(
        'INSERT INTO turnover_codes (seal, code, segment) VALUES (?, ?, ?)',
        ((seal, integer_text(code), segment) for code, segment in table.entries),
    )
    for batch in control.batches:
        db.execute('''
            INSERT INTO turnover_batches (seal, batch_id, table_id) VALUES (?, ?, ?)
        ''', (seal, batch.batch_id, control.table_id))
        db.executemany('''
            INSERT INTO turnover_pages (seal, batch_id, sequence, page_id, byte_length, body_digest)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ((seal, batch.batch_id, page.sequence, page.page_id,
               page.byte_length, page.body_digest) for page in batch.pages))
        save_directory(
            db, batch.batch_id, control.table_id, control.evidence.table_digest,
            tuple((page.sequence, page.page_id, page.byte_length, page.body_digest)
                  for page in batch.pages),
        )


def check_control_conflicts(db: sqlite3.Connection, control: TurnoverControl) -> None:
    """Reject same batch ids that carry a different original-page identity."""
    for batch in control.batches:
        bound = db.execute('''
            SELECT seal FROM turnover_batches WHERE batch_id = ?
        ''', (batch.batch_id,)).fetchone()
        if bound is not None and bound[0] != control.seal:
            raise ValueError('该批已绑定另一冻结截面')
        signature = directory_signature(db, batch.batch_id)
        if signature is None:
            continue
        table_id, table_digest, pages = signature
        candidate = (
            control.table_id, control.evidence.table_digest,
            tuple((page.sequence, page.page_id, page.byte_length, page.body_digest)
                  for page in batch.pages),
        )
        if (table_id, table_digest, pages) != candidate:
            raise ValueError('同批原件身份与已实收或已接管清册冲突')
