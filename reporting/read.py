import sqlite3

from reporting.snapshots import SegmentReport, load_snapshot


def read_report(db: sqlite3.Connection, report_id: str) -> SegmentReport:
    if not isinstance(report_id, str):
        raise KeyError(report_id)
    report = load_snapshot(db, report_id)
    if report is None:
        raise KeyError(report_id)
    return report
