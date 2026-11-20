from base64 import b64decode, b64encode
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
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
from receiving.rows import load_accepted_rows
from reporting.publish import publish_report
from storage import open_device, open_receiving
from tables.register import register_table


def payload_for(db, rows):
    register_table(db, 'A', {7: '预热'})
    open_batch(db, 'A01', 'A')
    for page_id, milliseconds in rows:
        append_row(db, 'A01', page_id, 7, milliseconds)
    seal_batch(db, 'A01')
    return export_batch(db, 'A01')


class HandoffTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.device = open_device(self.root / 'device.sqlite')
        self.receiver = open_receiving(self.root / 'receiving.sqlite')
        self.addCleanup(lambda: self.device.close())
        self.addCleanup(lambda: self.receiver.close())

    def reopen_receiver(self):
        self.receiver.close()
        self.receiver = open_receiving(self.root / 'receiving.sqlite')

    def standard_payload(self):
        return payload_for(self.device, [('P20', 11), ('P03', 3), ('P20', 0)])

    def assert_unaccepted(self):
        self.assertIsNone(find_receipt(self.receiver, 'A01'))
        self.assertEqual(load_accepted_rows(self.receiver, 'A01'), ())
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM accepted_rows').fetchone(), (0,))
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM accepted_batches').fetchone(), (0,))

    def reject_document(self, document):
        with self.assertRaises(ValueError):
            receive_batch(self.receiver, json.dumps(document).encode('utf-8'))
        self.assert_unaccepted()

    def test_offline_repeat(self):
        payload = self.standard_payload()
        self.device.close()
        first = receive_batch(self.receiver, payload)
        self.assertEqual(first.table_id, 'A')
        self.assertEqual(receive_batch(self.receiver, payload), first)
        rows = load_accepted_rows(self.receiver, 'A01')
        self.assertEqual([(r.batch_id, r.table_id, r.sequence, r.page_id, r.line_no, r.code,
                           r.segment, r.milliseconds) for r in rows], [
            ('A01', 'A', 1, 'P20', 1, 7, '预热', 11),
            ('A01', 'A', 1, 'P20', 2, 7, '预热', 0),
            ('A01', 'A', 2, 'P03', 1, 7, '预热', 3),
        ])
        self.assertEqual(publish_report(self.receiver, 'A01').totals, (('预热', 14),))

    def test_stable_export(self):
        payload = self.standard_payload()
        self.device.close()
        self.device = open_device(self.root / 'device.sqlite')
        self.assertEqual(export_batch(self.device, 'A01'), payload)
        self.assertEqual(self.device.execute('SELECT payload FROM exported_batches').fetchone(), (payload,))
        expanded = json.dumps(json.loads(payload), indent=2, sort_keys=True).encode('utf-8')
        self.assertNotEqual(expanded, payload)
        self.assertEqual(receive_batch(self.receiver, payload), receive_batch(self.receiver, expanded))

    def test_reopen_replay_and_same_batch_different_content(self):
        payload = self.standard_payload()
        with closing(open_device(self.root / 'other-device.sqlite')) as device:
            other = payload_for(device, [('P20', 11), ('P03', 9), ('P20', 0)])
        self.device.close()
        receive_batch(self.receiver, payload)
        persisted = find_receipt(self.receiver, 'A01')
        self.assertIsNotNone(persisted)
        self.reopen_receiver()
        replay = receive_batch(self.receiver, payload)
        self.assertEqual(replay, persisted)
        with self.assertRaises(ValueError):
            receive_batch(self.receiver, other)
        self.assertEqual(find_receipt(self.receiver, 'A01'), persisted)
        self.assertEqual([row.milliseconds for row in load_accepted_rows(self.receiver, 'A01')], [11, 0, 3])
        self.assertEqual(publish_report(self.receiver, 'A01').totals, (('预热', 14),))

    def test_equal_bodies_distinct_pages(self):
        payload = payload_for(self.device, [('P20', 11), ('P03', 11)])
        first, second = json.loads(payload)['pages']
        self.assertEqual(first['body'], second['body'])
        self.assertNotEqual(first['page_id'], second['page_id'])
        receive_batch(self.receiver, payload)
        report = publish_report(self.receiver, 'A01')
        self.assertEqual(report.totals, (('预热', 22),))
        self.assertEqual([(row.page_id, row.line_no) for row in report.contributions], [('P20', 1), ('P03', 1)])

    def test_empty_exchange(self):
        payload = payload_for(self.device, [])
        document = json.loads(payload)
        self.assertEqual(document['pages'], [])
        self.assertEqual(document['manifest']['pages'], [])
        first = receive_batch(self.receiver, payload)
        self.reopen_receiver()
        self.assertEqual(receive_batch(self.receiver, payload), first)
        self.assertEqual(load_accepted_rows(self.receiver, 'A01'), ())

    def test_missing_or_changed_page(self):
        document = json.loads(self.standard_payload())
        missing = deepcopy(document)
        missing['pages'].pop()
        self.reject_document(missing)
        changed = deepcopy(document)
        original = b64decode(changed['pages'][0]['body'])
        body = original.replace(b'"milliseconds":11', b'"milliseconds":12', 1)
        self.assertNotEqual(body, original)
        changed['pages'][0]['body'] = b64encode(body).decode('ascii')
        self.reject_document(changed)

    def test_page_bijection_and_binding(self):
        document = json.loads(self.standard_payload())
        first, second = document['pages']
        for pages in ([first, first], [second, first], [first, second, second],
                      [first, dict(second, table_id='B')], [first, dict(second, batch_id='A02')],
                      [dict(first, sequence=True), second]):
            with self.subTest(pages=pages):
                altered = deepcopy(document)
                altered['pages'] = pages
                self.reject_document(altered)
        repeated = deepcopy(document)
        repeated['manifest']['pages'][1]['page_id'] = first['page_id']
        repeated['pages'][1]['page_id'] = first['page_id']
        self.reject_document(repeated)

    def test_original_bytes(self):
        document = json.loads(self.standard_payload())
        page = document['pages'][1]
        original = b64decode(page['body'])
        altered = b'[{"code":7,"line_no":1,"milliseconds":3}]'
        self.assertEqual(len(altered), len(original))
        self.assertEqual(json.loads(altered), json.loads(original))
        self.assertNotEqual(altered, original)
        page['body'] = b64encode(altered).decode('ascii')
        self.reject_document(document)

    def test_table_binding(self):
        original = json.loads(self.standard_payload())
        changed = deepcopy(original)
        changed['table']['entries'][0][1] = '稳态'
        self.reject_document(changed)
        wrong_id = deepcopy(original)
        wrong_id['table']['table_id'] = 'B'
        self.reject_document(wrong_id)
        wrong_version = deepcopy(original)
        wrong_version['version'] = True
        self.reject_document(wrong_version)
        broken = deepcopy(original)
        broken['pages'][0]['body'] = '++:invalid'
        self.reject_document(broken)
        del changed['manifest']['pages']
        self.reject_document(changed)

    def test_invalid_measurement_even_with_matching_digest(self):
        original = json.loads(self.standard_payload())
        invalid_rows = (
            b'[{"line_no":1,"code":99,"milliseconds":3}]',
            b'[{"line_no":1,"code":7,"milliseconds":-3}]',
            b'[{"line_no":1,"code":7,"milliseconds":false}]',
            b'[{"line_no":1,"code":7,"milliseconds":3.0}]',
            b'[{"line_no":1,"code":7,"milliseconds":"3"}]',
            b'[{"line_no":1,"code":7,"milliseconds":null}]',
            b'[{"line_no":true,"code":7,"milliseconds":3}]',
            b'[{"line_no":2,"code":7,"milliseconds":3}]',
            b'[{"line_no":1,"code":7,"code":99,"milliseconds":3}]',
        )
        for body in invalid_rows:
            with self.subTest(body=body):
                document = deepcopy(original)
                document['pages'][1]['body'] = b64encode(body).decode('ascii')
                entry = document['manifest']['pages'][1]
                entry.update(byte_length=len(body), body_digest=sha256(body).hexdigest())
                self.reject_document(document)

    def test_invalid_export_source(self):
        register_table(self.device, 'A', {7: '预热'})
        open_batch(self.device, 'A01', 'A')
        with self.assertRaises(ValueError):
            export_batch(self.device, 'A01')
        append_row(self.device, 'A01', 'P20', 7, 11)
        append_row(self.device, 'A01', 'P03', 7, 3)
        seal_batch(self.device, 'A01')
        self.device.execute("DELETE FROM issued_pages WHERE batch_id = 'A01' AND page_id = 'P03'")
        with self.assertRaises(ValueError):
            export_batch(self.device, 'A01')
        self.assertEqual(self.device.execute('SELECT count(*) FROM exported_batches').fetchone(), (0,))

    def test_retry_after_failed_commit(self):
        payload = self.standard_payload()
        self.device.close()
        self.receiver.execute('''
            CREATE TEMP TRIGGER stop_accept BEFORE INSERT ON accepted_rows
            WHEN NEW.line_no = 2 BEGIN SELECT RAISE(ABORT, 'accept interrupted'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'accept interrupted'):
            receive_batch(self.receiver, payload)
        self.reopen_receiver()
        self.assert_unaccepted()
        with self.assertRaises(ValueError):
            publish_report(self.receiver, 'A01')
        accepted = receive_batch(self.receiver, payload)
        self.assertEqual(find_receipt(self.receiver, 'A01'), accepted)
        self.assertEqual(receive_batch(self.receiver, payload), accepted)
        self.assertEqual([row.milliseconds for row in load_accepted_rows(self.receiver, 'A01')], [11, 0, 3])
        self.assertEqual(publish_report(self.receiver, 'A01').totals, (('预热', 14),))
