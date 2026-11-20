import sqlite3

from receiving.receipts import find_receipt
from receiving.rows import load_accepted_rows
from reporting.aggregate import aggregate_segments
from reporting.contributions import build_contributions
from reporting.snapshots import SegmentReport, load_snapshot, save_snapshot
from storage import write_transaction
from tables.validate import require_text


def publish_report(db: sqlite3.Connection, batch_id: str) -> SegmentReport:
    require_text(batch_id, '批号')
    with write_transaction(db):
        receipt = find_receipt(db, batch_id)
        if receipt is None:
            raise ValueError('只可为已实收批发布工段账')
        report_id = 'report-' + receipt.content_id
        existing = load_snapshot(db, report_id)
        if existing is not None:
            return existing
        contributions = build_contributions(load_accepted_rows(db, batch_id))
        report = SegmentReport(report_id, batch_id, receipt.table_id,
                               aggregate_segments(contributions), contributions)
        save_snapshot(db, report)
    return report
