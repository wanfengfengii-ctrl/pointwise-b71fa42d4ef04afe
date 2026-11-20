from dataclasses import dataclass
import sqlite3

from reporting.contributions import Contribution
from storage import integer_text, restore_integer


@dataclass(frozen=True, slots=True)
class SegmentReport:
    report_id: str
    batch_id: str
    table_id: str
    totals: tuple[tuple[str, int], ...]
    contributions: tuple[Contribution, ...]


def initialize_reports(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT PRIMARY KEY NOT NULL,
            batch_id TEXT NOT NULL UNIQUE REFERENCES accepted_batches(batch_id),
            table_id TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS report_totals (
            report_id TEXT NOT NULL REFERENCES reports(report_id),
            position INTEGER NOT NULL,
            segment TEXT NOT NULL,
            milliseconds TEXT NOT NULL,
            PRIMARY KEY (report_id, position),
            UNIQUE (report_id, segment)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS report_contributions (
            report_id TEXT NOT NULL REFERENCES reports(report_id),
            sequence INTEGER NOT NULL,
            page_id TEXT NOT NULL,
            line_no INTEGER NOT NULL,
            code TEXT NOT NULL,
            segment TEXT NOT NULL,
            milliseconds TEXT NOT NULL,
            PRIMARY KEY (report_id, sequence, line_no)
        )
    ''')


def save_snapshot(db: sqlite3.Connection, report: SegmentReport) -> None:
    db.execute('INSERT INTO reports (report_id, batch_id, table_id) VALUES (?, ?, ?)',
               (report.report_id, report.batch_id, report.table_id))
    db.executemany('''
        INSERT INTO report_totals (report_id, position, segment, milliseconds) VALUES (?, ?, ?, ?)
    ''', ((report.report_id, position, segment, integer_text(milliseconds))
          for position, (segment, milliseconds) in enumerate(report.totals, 1)))
    db.executemany('''
        INSERT INTO report_contributions (report_id, sequence, page_id, line_no, code, segment, milliseconds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ((report.report_id, row.sequence, row.page_id, row.line_no, integer_text(row.code),
           row.segment, integer_text(row.milliseconds)) for row in report.contributions))


def load_snapshot(db: sqlite3.Connection, report_id: str) -> SegmentReport | None:
    header = db.execute('SELECT batch_id, table_id FROM reports WHERE report_id = ?', (report_id,)).fetchone()
    if header is None:
        return None
    batch_id, table_id = header
    totals = tuple((segment, restore_integer(milliseconds)) for segment, milliseconds in db.execute('''
        SELECT segment, milliseconds FROM report_totals WHERE report_id = ? ORDER BY position
    ''', (report_id,)))
    observations = db.execute('''
        SELECT sequence, page_id, line_no, code, segment, milliseconds
        FROM report_contributions WHERE report_id = ? ORDER BY sequence, line_no
    ''', (report_id,))
    contributions = tuple(
        Contribution(batch_id, table_id, sequence, page_id, line_no, restore_integer(code),
                     segment, restore_integer(milliseconds))
        for sequence, page_id, line_no, code, segment, milliseconds in observations
    )
    return SegmentReport(report_id, batch_id, table_id, totals, contributions)
