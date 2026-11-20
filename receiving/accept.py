import sqlite3

from exchange.decode import decode_exchange
from exchange.integrity import digest_bytes
from exchange.turnover import (
    TurnoverControl, build_receipt, parse_control,
)
from device.issue import IssuedPage
from device.page_body import decode_page_body
from receiving.handoff import (
    check_control_conflicts, complete_split_batch, find_directory,
    find_turnover_batch, persist_turnover, save_directory,
    translate_pending_page,
)
from receiving.receipts import Receipt, find_receipt, insert_receipt
from receiving.reconcile import translate_batch
from receiving.rows import insert_accepted_rows
from storage import write_transaction
from tables.validate import require_text


def accept_turnover(db: sqlite3.Connection, control: bytes) -> bytes:
    """Persist a complete frozen section and return its standalone receipt."""
    parsed = parse_control(control)
    receipt_bytes = build_receipt(parsed)
    with write_transaction(db):
        existing = db.execute(
            'SELECT receipt FROM turnovers WHERE seal = ?', (parsed.seal,)
        ).fetchone()
        if existing is not None:
            # 同一件在接班库重开后精确复取原回执。
            return existing[0]
        check_control_conflicts(db, parsed)
        persist_turnover(db, parsed, control, receipt_bytes)
        _promote_staged_pages(db, parsed)
        for batch in parsed.batches:
            # 空批凭接管清册明确的空目录取得齐批资格；其余批在暂存页齐备时齐批。
            _complete_if_ready(db, parsed, batch.batch_id)
    return receipt_bytes


def _promote_staged_pages(db: sqlite3.Connection, control: TurnoverControl) -> None:
    by_batch = {batch.batch_id: batch for batch in control.batches}
    staged = db.execute('''
        SELECT batch_id, table_id, sequence, page_id, body, body_digest
        FROM staged_pages
    ''').fetchall()
    for batch_id, table_id, sequence, page_id, body, body_digest in staged:
        batch = by_batch.get(batch_id)
        if batch is None:
            continue
        if table_id != control.table_id:
            raise ValueError('暂存页与接管表代不符')
        entry = next((candidate for candidate in batch.pages
                      if candidate.page_id == page_id), None)
        if entry is None or entry.sequence != sequence:
            raise ValueError('暂存页不在接管截面之内')
        if len(body) != entry.byte_length or body_digest != entry.body_digest \
                or digest_bytes(body) != entry.body_digest:
            raise ValueError('暂存原页字节与冻结截面不符')
        db.execute('''
            INSERT INTO pending_pages (batch_id, table_id, sequence, page_id, body, body_digest)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (batch_id, control.table_id, sequence, page_id, body, body_digest))
        db.execute('DELETE FROM staged_pages WHERE batch_id = ? AND page_id = ?',
                   (batch_id, page_id))


def _complete_if_ready(db: sqlite3.Connection, control: TurnoverControl, batch_id: str) -> None:
    batch = next(item for item in control.batches if item.batch_id == batch_id)
    pending_count = db.execute(
        'SELECT count(*) FROM pending_pages WHERE batch_id = ?', (batch_id,)
    ).fetchone()[0]
    if find_receipt(db, batch_id) is not None:
        return
    if pending_count == len(batch.pages):
        complete_split_batch(
            db, control.seal, batch_id, control.table_id,
            control.evidence.table_digest,
        )


def _validate_page_identity(page, body_digest: str) -> None:
    if not isinstance(page, IssuedPage):
        raise ValueError('收页入口须接收 IssuedPage')
    require_text(page.batch_id, '批号')
    require_text(page.table_id, '表号')
    require_text(page.page_id, '页号')
    if type(page.sequence) is not int or page.sequence <= 0:
        raise ValueError('发页序须为正整数')
    if type(page.body) is not bytes or not page.body:
        raise ValueError('原页正文须为非空字节')
    if type(body_digest) is not str or digest_bytes(page.body) != body_digest:
        raise ValueError('正文摘要与原字节不符')
    decode_page_body(page.body)  # 非法行在此拒绝。


def accept_page(db: sqlite3.Connection, page: IssuedPage,
                body_digest: str):
    _validate_page_identity(page, body_digest)
    with write_transaction(db):
        receipt = find_receipt(db, page.batch_id)
        if receipt is not None:
            return _handle_accepted_batch_page(db, page, body_digest)
        known = find_turnover_batch(db, page.batch_id)
        if known is not None:
            return _handle_known_page(db, page, body_digest, known)
        return _stage_unknown_page(db, page, body_digest)


def _handle_accepted_batch_page(db: sqlite3.Connection, page: IssuedPage,
                                body_digest: str):
    directory = find_directory(db, page.batch_id)
    if directory is None:
        raise ValueError('已实收批缺少原件身份清册')
    (table_id, _), entries = directory
    entry = next((item for item in entries if item[1] == page.page_id), None)
    if entry is None:
        raise ValueError('该页不在已实收批的原页目录之内')
    sequence, _, byte_length, expected_digest = entry
    if page.table_id != table_id or page.sequence != sequence:
        raise ValueError('原页身份与已实收目录不符')
    if len(page.body) != byte_length or body_digest != expected_digest:
        raise ValueError('同身份原页内容不一致')
    from receiving.rows import load_accepted_rows

    rows = tuple(row for row in load_accepted_rows(db, page.batch_id)
                 if row.page_id == page.page_id)
    if not rows:
        raise ValueError('已实收批缺少该原页的计量行')
    return rows


def _handle_known_page(db: sqlite3.Connection, page: IssuedPage, body_digest: str,
                       known) -> tuple | None:
    seal, table_id, table_digest = known
    if page.table_id != table_id:
        raise ValueError('同批错代：页表号与接管截面不符')
    directory = find_directory(db, page.batch_id)
    (_, _), entries = directory
    entry = next((item for item in entries if item[1] == page.page_id), None)
    if entry is None:
        raise ValueError('页号不在接管截面之内')
    sequence, _, byte_length, expected_digest = entry
    if page.sequence != sequence:
        raise ValueError('错发页序：与冻结目录不一致')
    if len(page.body) != byte_length or body_digest != expected_digest:
        raise ValueError('摘要或字节与冻结截面不符')

    pending = db.execute('''
        SELECT table_id, sequence, body, body_digest FROM pending_pages
        WHERE batch_id = ? AND page_id = ?
    ''', (page.batch_id, page.page_id)).fetchone()
    if pending is not None:
        pending_table, pending_sequence, pending_body, pending_digest = pending
        if (pending_table != page.table_id or pending_sequence != page.sequence
                or pending_body != page.body or pending_digest != body_digest):
            raise ValueError('同身份异内容：原页已按另一字节暂收')
        # 合法重投复领首次结果。
        return translate_pending_page(db, seal, table_id, page)

    db.execute('''
        INSERT INTO pending_pages (batch_id, table_id, sequence, page_id, body, body_digest)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (page.batch_id, table_id, page.sequence, page.page_id, page.body, body_digest))
    rows = translate_pending_page(db, seal, table_id, page)
    turnover = db.execute(
        'SELECT count(*) FROM turnover_pages WHERE seal = ? AND batch_id = ?',
        (seal, page.batch_id),
    ).fetchone()[0]
    pending_count = db.execute(
        'SELECT count(*) FROM pending_pages WHERE batch_id = ?', (page.batch_id,)
    ).fetchone()[0]
    if pending_count == turnover:
        complete_split_batch(db, seal, page.batch_id, table_id, table_digest)
    return rows


def _stage_unknown_page(db: sqlite3.Connection, page: IssuedPage,
                        body_digest: str) -> None:
    existing = db.execute('''
        SELECT table_id, sequence, body, body_digest FROM staged_pages
        WHERE batch_id = ? AND page_id = ?
    ''', (page.batch_id, page.page_id)).fetchone()
    if existing is not None:
        if existing != (page.table_id, page.sequence, page.body, body_digest):
            raise ValueError('同身份异内容：暂存页已按另一字节到达')
        return None
    db.execute('''
        INSERT INTO staged_pages (batch_id, table_id, sequence, page_id, body, body_digest)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (page.batch_id, page.table_id, page.sequence, page.page_id, page.body, body_digest))
    return None


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
        check_whole_piece_conflicts(db, exchange)
        receipt = Receipt(manifest.batch_id, manifest.table_id, exchange.content_id,
                          'receipt-' + exchange.content_id)
        insert_receipt(db, receipt)
        insert_accepted_rows(db, rows)
        save_directory(
            db, manifest.batch_id, manifest.table_id, manifest.table_digest,
            tuple((entry.sequence, entry.page_id, entry.byte_length, entry.body_digest)
                  for entry in manifest.pages),
        )
        db.execute('DELETE FROM pending_pages WHERE batch_id = ?', (manifest.batch_id,))
        db.execute('DELETE FROM staged_pages WHERE batch_id = ?', (manifest.batch_id,))
    return receipt


def check_whole_piece_conflicts(db: sqlite3.Connection, exchange) -> None:
    manifest = exchange.manifest
    signature = (
        manifest.table_id, manifest.table_digest,
        tuple((entry.sequence, entry.page_id, entry.byte_length, entry.body_digest)
              for entry in manifest.pages),
    )
    known = find_directory(db, manifest.batch_id)
    if known is not None:
        (table_id, table_digest), pages = known
        if (table_id, table_digest, tuple(pages)) != signature:
            raise ValueError('整件原件身份与已接管或已实收清册冲突')
    # 分路暂存页若已到，须与整件身份一致。
    staged = db.execute('''
        SELECT table_id, sequence, page_id, body_digest
        FROM staged_pages WHERE batch_id = ?
    ''', (manifest.batch_id,)).fetchall()
    for table_id, sequence, page_id, body_digest in staged:
        if table_id != manifest.table_id:
            raise ValueError('暂存分路页与整件表代不符')
        entry = next((item for item in manifest.pages if item.page_id == page_id), None)
        if entry is None or entry.sequence != sequence or entry.body_digest != body_digest:
            raise ValueError('暂存分路页与整件原页身份不符')
