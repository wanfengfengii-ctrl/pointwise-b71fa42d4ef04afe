from pathlib import Path
import sqlite3
import tempfile
import unittest

from device.active_table import read_active_table
from device.batches import load_batch, open_batch
from device.issue import load_issued_pages
from device.page_body import SourceRow
from device.record import append_row
from device.seal import seal_batch
from exchange.decode import decode_exchange
from exchange.export import export_batch
from storage import open_device
from tables.catalog import load_table
from tables.register import register_table
from tables.resolve import resolve_code


class RecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'device.sqlite'
        self.db = open_device(self.path)
        self.addCleanup(lambda: self.db.close())

    def reopen(self):
        self.db.close()
        self.db = open_device(self.path)

    def start_batch(self):
        register_table(self.db, 'A', {7: '预热'})
        open_batch(self.db, 'A01', 'A')

    def test_seal_in_source_order(self):
        self.start_batch()
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 11), 1)
        self.assertEqual(append_row(self.db, 'A01', 'P03', 7, 3), 1)
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 0), 2)
        sealed = seal_batch(self.db, 'A01')
        self.assertEqual(sealed.table_id, 'A')
        self.assertEqual([(page.sequence, page.page_id) for page in sealed.directory],
                         [(1, 'P20'), (2, 'P03')])
        self.reopen()
        self.assertEqual(seal_batch(self.db, 'A01'), sealed)
        parsed = decode_exchange(export_batch(self.db, 'A01'))
        self.assertEqual([page.rows for page in parsed.pages], [
            (SourceRow(1, 7, 11), SourceRow(2, 7, 0)), (SourceRow(1, 7, 3),),
        ])
        with self.assertRaises(ValueError):
            append_row(self.db, 'A01', 'P20', 7, 5)
        with self.assertRaises(ValueError):
            seal_batch(self.db, 'absent')

    def test_table_binding(self):
        self.assertEqual(register_table(self.db, 'A', [(7, '预热'), (-1, ' 诊断 ')]), 'A')
        self.assertEqual(register_table(self.db, 'A', [(-1, ' 诊断 '), (7, '预热'), (7, '预热')]), 'A')
        with self.assertRaises(ValueError):
            register_table(self.db, 'A', {7: '稳态', -1: ' 诊断 '})
        open_batch(self.db, 'A01', 'A')
        self.assertEqual(resolve_code(read_active_table(self.db), 7), '预热')
        self.assertEqual(resolve_code(read_active_table(self.db), -1), ' 诊断 ')
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 11), 1)

    def test_invalid_table_entries(self):
        bad_tables = ([], None, [(7, '预热'), (7, '稳态')], [(7, ' ')], [(7, 3)],
                      [(True, '预热')], [('7', '预热')], [(7.0, '预热')], [(7,)], [(7, '\ud800')])
        for entries in bad_tables:
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):
                    register_table(self.db, 'invalid', entries)
                self.assertIsNone(load_table(self.db, 'invalid'))
        with self.assertRaises(ValueError):
            register_table(self.db, '', {7: '预热'})
        self.assertEqual(register_table(self.db, 'invalid', {7: '预热'}), 'invalid')

    def test_reinitialize_catalog(self):
        self.start_batch()
        append_row(self.db, 'A01', 'P20', 7, 11)
        self.reopen()
        self.assertEqual(read_active_table(self.db).entries, ((7, '预热'),))
        self.assertEqual(register_table(self.db, 'A', {7: '预热'}), 'A')
        with self.assertRaises(ValueError):
            register_table(self.db, 'A', {7: '稳态'})
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 0), 2)
        seal_batch(self.db, 'A01')
        exchange = decode_exchange(export_batch(self.db, 'A01'))
        self.assertEqual(exchange.pages[0].rows, (SourceRow(1, 7, 11), SourceRow(2, 7, 0)))

    def test_rejected_row_has_no_position(self):
        self.start_batch()
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 11), 1)
        with self.assertRaises(ValueError):
            append_row(self.db, 'A01', 'P20', 99, 5)
        with self.assertRaises(ValueError):
            append_row(self.db, 'A01', 'P20', 7, -1)
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 0), 2)
        seal_batch(self.db, 'A01')
        parsed = decode_exchange(export_batch(self.db, 'A01'))
        self.assertEqual(parsed.pages[0].rows, (SourceRow(1, 7, 11), SourceRow(2, 7, 0)))

    def test_invalid_first_rows_do_not_allocate(self):
        self.start_batch()
        bad_values = [(99, 1), (True, 1), (7.0, 1), ('7', 1), (None, 1),
                      (7, -1), (7, False), (7, 1.0), (7, '1'), (7, None)]
        for code, milliseconds in bad_values:
            with self.subTest(code=code, milliseconds=milliseconds):
                with self.assertRaises(ValueError):
                    append_row(self.db, 'A01', 'P03', code, milliseconds)
        with self.assertRaises(ValueError):
            append_row(self.db, 'absent', 'P03', 7, 1)
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 0), 1)
        self.assertEqual(append_row(self.db, 'A01', 'P03', 7, 3), 1)
        sealed = seal_batch(self.db, 'A01')
        self.assertEqual([(page.page_id, page.sequence) for page in sealed.directory], [('P20', 1), ('P03', 2)])

    def test_closed_batch_switch(self):
        self.start_batch()
        register_table(self.db, 'B', {7: '稳态'})
        with self.assertRaises(ValueError):
            open_batch(self.db, 'A02', 'B')
        self.assertIsNone(load_batch(self.db, 'A02'))
        self.assertEqual(read_active_table(self.db).table_id, 'A')
        append_row(self.db, 'A01', 'P20', 7, 11)
        prior = seal_batch(self.db, 'A01')
        with self.assertRaises(ValueError):
            open_batch(self.db, 'A02', 'unknown')
        with self.assertRaises(ValueError):
            open_batch(self.db, 'A01', 'B')
        self.assertEqual(read_active_table(self.db).table_id, 'A')
        self.assertEqual(open_batch(self.db, 'A02', 'B').table_id, 'B')
        self.assertEqual(read_active_table(self.db).table_id, 'B')
        self.assertEqual(seal_batch(self.db, 'A01'), prior)
        self.assertEqual(decode_exchange(export_batch(self.db, 'A01')).table.entries, ((7, '预热'),))

    def test_empty_batch(self):
        self.start_batch()
        empty = seal_batch(self.db, 'A01')
        self.assertEqual(empty.directory, ())
        self.reopen()
        self.assertEqual(seal_batch(self.db, 'A01'), empty)
        self.assertEqual(load_issued_pages(self.db, 'A01'), ())

    def test_seal_rollback(self):
        self.start_batch()
        append_row(self.db, 'A01', 'P20', 7, 11)
        append_row(self.db, 'A01', 'P03', 7, 3)
        self.db.execute('''
            CREATE TEMP TRIGGER stop_seal BEFORE INSERT ON sealed_directory
            WHEN NEW.sequence = 2 BEGIN SELECT RAISE(ABORT, 'seal interrupted'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'seal interrupted'):
            seal_batch(self.db, 'A01')
        self.reopen()
        self.assertIsNone(load_batch(self.db, 'A01').directory)
        self.assertEqual(load_issued_pages(self.db, 'A01'), ())
        self.assertEqual(self.db.execute('SELECT count(*) FROM sealed_directory').fetchone(), (0,))
        self.assertEqual(append_row(self.db, 'A01', 'P20', 7, 0), 2)
        self.assertEqual([entry.page_id for entry in seal_batch(self.db, 'A01').directory], ['P20', 'P03'])
