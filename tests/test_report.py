from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from device.batches import open_batch
from device.record import append_row
from device.seal import seal_batch
from exchange.export import export_batch
from receiving.accept import receive_batch
from receiving.receipts import find_receipt
from reporting.publish import publish_report
from reporting.read import read_report
from storage import open_device, open_receiving
from tables.register import register_table


class ReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.receiver = open_receiving(self.root / 'receiving.sqlite')
        self.addCleanup(lambda: self.receiver.close())

    def reopen_receiver(self):
        self.receiver.close()
        self.receiver = open_receiving(self.root / 'receiving.sqlite')

    def transfer(self, batch, table, mapping, rows):
        with closing(open_device(self.root / 'device.sqlite')) as device:
            register_table(device, table, mapping)
            open_batch(device, batch, table)
            for page, code, milliseconds in rows:
                append_row(device, batch, page, code, milliseconds)
            seal_batch(device, batch)
            payload = export_batch(device, batch)
        return receive_batch(self.receiver, payload)

    def test_repeated_codes_and_zero(self):
        self.transfer('A01', 'A', {7: '预热'}, [('P20', 7, 11), ('P03', 7, 3), ('P20', 7, 0)])
        report = publish_report(self.receiver, 'A01')
        self.assertEqual(report.totals, (('预热', 14),))
        self.assertEqual([(c.sequence, c.page_id, c.line_no, c.code, c.milliseconds)
                          for c in report.contributions], [(1, 'P20', 1, 7, 11),
                                                          (1, 'P20', 2, 7, 0),
                                                          (2, 'P03', 1, 7, 3)])
        self.assertEqual(read_report(self.receiver, report.report_id), report)

    def test_zero_only_segment_and_first_contribution_order(self):
        self.transfer('A01', 'A', {2: '停机', 7: '预热', 9: '稳态'},
                      [('P20', 9, 0), ('P03', 7, 4), ('P20', 7, 3)])
        report = publish_report(self.receiver, 'A01')
        self.assertEqual(report.totals, (('稳态', 0), ('预热', 7)))
        self.assertEqual([(c.segment, c.milliseconds) for c in report.contributions],
                         [('稳态', 0), ('预热', 3), ('预热', 4)])

    def test_exact_integer(self):
        huge = 10 ** 5000
        code = -(huge + 9)
        self.transfer('A01', 'A', {code: '预热'}, [('P20', code, huge), ('P03', code, 17)])
        published = publish_report(self.receiver, 'A01')
        self.reopen_receiver()
        report = read_report(self.receiver, published.report_id)
        self.assertTrue(report.totals == (('预热', huge + 17),), '大整数合计失真')
        self.assertTrue(tuple(c.milliseconds for c in report.contributions) == (huge, 17), '原毫秒失真')
        self.assertTrue(tuple(c.code for c in report.contributions) == (code, code), '原代码失真')

    def test_empty_report(self):
        self.transfer('A01', 'A', {7: '预热'}, [])
        report = publish_report(self.receiver, 'A01')
        self.assertEqual(report.totals, ())
        self.assertEqual(report.contributions, ())
        self.reopen_receiver()
        self.assertEqual(read_report(self.receiver, report.report_id), report)

    def test_report_after_next_batch(self):
        self.transfer('A01', 'A', {7: '预热'}, [('P20', 7, 11), ('P20', 7, 0), ('P03', 7, 3)])
        old = publish_report(self.receiver, 'A01')
        self.transfer('A02', 'B', {7: '稳态'}, [('P20', 7, 11)])
        new = publish_report(self.receiver, 'A02')
        self.assertNotEqual(old.report_id, new.report_id)
        self.reopen_receiver()
        self.assertEqual(read_report(self.receiver, old.report_id), old)
        self.assertEqual(read_report(self.receiver, new.report_id), new)
        self.assertEqual(old.totals, (('预热', 14),))
        self.assertEqual(new.totals, (('稳态', 11),))
        self.assertEqual([(c.batch_id, c.table_id, c.page_id, c.line_no, c.segment)
                          for c in old.contributions], [('A01', 'A', 'P20', 1, '预热'),
                                                       ('A01', 'A', 'P20', 2, '预热'),
                                                       ('A01', 'A', 'P03', 1, '预热')])
        self.assertEqual([(c.batch_id, c.table_id, c.page_id, c.line_no, c.segment)
                          for c in new.contributions], [('A02', 'B', 'P20', 1, '稳态')])
        self.assertEqual(publish_report(self.receiver, 'A01'), old)
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM reports').fetchone(), (2,))
        with self.assertRaises(KeyError):
            read_report(self.receiver, 'unknown-report')

    def test_publish_rollback(self):
        self.transfer('A01', 'A', {7: '预热'}, [('P20', 7, 11)])
        old = publish_report(self.receiver, 'A01')
        receipt = self.transfer('A02', 'B', {7: '稳态'}, [('P20', 7, 11), ('P20', 7, 3)])
        self.receiver.execute('''
            CREATE TEMP TRIGGER stop_report BEFORE INSERT ON report_contributions
            WHEN NEW.line_no = 2 BEGIN SELECT RAISE(ABORT, 'report interrupted'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'report interrupted'):
            publish_report(self.receiver, 'A02')
        self.reopen_receiver()
        for table in ('reports', 'report_totals', 'report_contributions'):
            self.assertEqual(self.receiver.execute(f'SELECT count(*) FROM {table}').fetchone(), (1,))
        self.assertEqual(find_receipt(self.receiver, 'A02'), receipt)
        self.assertEqual(read_report(self.receiver, old.report_id), old)
        with self.assertRaises(KeyError):
            read_report(self.receiver, 'report-' + receipt.content_id)
        current = publish_report(self.receiver, 'A02')
        self.assertEqual(current.totals, (('稳态', 14),))
        self.assertEqual([c.milliseconds for c in current.contributions], [11, 3])
        self.assertEqual(read_report(self.receiver, current.report_id), current)
