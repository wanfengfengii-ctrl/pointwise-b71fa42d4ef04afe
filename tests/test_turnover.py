import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from device.active_table import read_active_table
from device.batches import open_batch
from device.issue import IssuedPage, load_issued_pages, prepare_turnover
from device.active_table import activate_table
from device.record import append_row
from device.seal import seal_batch
from exchange.export import export_batch
from exchange.integrity import digest_bytes
from exchange.turnover import parse_control
from receiving.accept import accept_page, accept_turnover, receive_batch
from receiving.receipts import find_receipt
from receiving.rows import load_accepted_rows
from reporting.publish import publish_report
from reporting.read import read_report
from storage import open_device, open_receiving
from tables.catalog import load_table
from tables.register import register_table


def deliver(db, page):
    return accept_page(db, page, digest_bytes(page.body))


class TurnoverTestBase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.device_path = self.root / 'device.sqlite'
        self.receiving_path = self.root / 'receiving.sqlite'

    def open_device(self):
        db = open_device(self.device_path)
        self.addCleanup(lambda: db.close())
        return db

    def open_receiver(self):
        db = open_receiving(self.receiving_path)
        self.addCleanup(lambda: db.close())
        return db

    def build_generation_a(self, device, batch_id='A01'):
        register_table(device, 'A', {7: '预热', 8: '检查'})
        register_table(device, 'B', {7: '稳态'})
        open_batch(device, batch_id, 'A')
        append_row(device, batch_id, 'P', 7, 11)
        append_row(device, batch_id, 'R', 8, 3)
        seal_batch(device, batch_id)
        return load_issued_pages(device, batch_id)


class MainChainTests(TurnoverTestBase):
    def test_public_sample_chain(self):
        with closing(open_device(self.device_path)) as device:
            p, r = self.build_generation_a(device)
            control_a = prepare_turnover(device, 'A')

        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, control_a)  # 答复丢失
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt_a = accept_turnover(receiver, control_a)
            self.assertEqual(accept_turnover(receiver, control_a), receipt_a)

        with closing(open_device(self.device_path)) as device:
            self.assertEqual(activate_table(device, 'B', receipt_a), 'B')
            open_batch(device, 'B01', 'B')
            append_row(device, 'B01', 'Q', 7, 11)
            seal_batch(device, 'B01')
            (q,) = load_issued_pages(device, 'B01')
            control_b = prepare_turnover(device, 'B')

        self.assertEqual(p.body, q.body)
        with closing(open_receiving(self.receiving_path)) as receiver:
            self.assertIsNone(deliver(receiver, q))  # 正文先到，待接管
            accept_turnover(receiver, control_b)
            (new_row,) = deliver(receiver, q)
            (old_row,) = deliver(receiver, p)
            self.assertEqual((old_row.page_id, old_row.segment, old_row.milliseconds),
                             ('P', '预热', 11))
            self.assertEqual((new_row.page_id, new_row.segment, new_row.milliseconds),
                             ('Q', '稳态', 11))
            with self.assertRaises(ValueError):
                publish_report(receiver, 'A01')  # 缺 R 不得签账
            deliver(receiver, r)
            self.assertEqual(deliver(receiver, p), (old_row,))  # 合法重投复领
            report_a = publish_report(receiver, 'A01')
            report_b = publish_report(receiver, 'B01')

        with closing(open_receiving(self.receiving_path)) as receiver:
            old = read_report(receiver, report_a.report_id)
            new = read_report(receiver, report_b.report_id)
            self.assertEqual(old.totals, (('预热', 11), ('检查', 3)))
            self.assertEqual(new.totals, (('稳态', 11),))
            self.assertEqual([(row.page_id, row.line_no) for row in old.contributions],
                             [('P', 1), ('R', 1)])
            self.assertEqual([(row.page_id, row.line_no) for row in new.contributions],
                             [('Q', 1)])

    def test_identical_bodies_counted_per_page(self):
        # P、Q 正文相同，仍分别计预热、稳态各 11 毫秒。
        with closing(open_device(self.device_path)) as device:
            p, _ = self.build_generation_a(device)
            control_a = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt_a = accept_turnover(receiver, control_a)
        with closing(open_device(self.device_path)) as device:
            activate_table(device, 'B', receipt_a)
            open_batch(device, 'B01', 'B')
            append_row(device, 'B01', 'Q', 7, 11)
            seal_batch(device, 'B01')
            (q,) = load_issued_pages(device, 'B01')
            control_b = prepare_turnover(device, 'B')
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, control_b)
            (q_row,) = deliver(receiver, q)
            (p_row,) = deliver(receiver, p)
            self.assertEqual(p.body, q.body)
            self.assertEqual((p_row.segment, p_row.milliseconds), ('预热', 11))
            self.assertEqual((q_row.segment, q_row.milliseconds), ('稳态', 11))


class FreezeAndSwapTests(TurnoverTestBase):
    def test_freeze_blocks_new_batches_and_repeat_control_stable(self):
        with closing(open_device(self.device_path)) as device:
            p, _ = self.build_generation_a(device)
            first = prepare_turnover(device, 'A')
            second = prepare_turnover(device, 'A')
            self.assertEqual(first, second)
            with self.assertRaises(ValueError):
                open_batch(device, 'A02', 'A')
            with self.assertRaises(ValueError):
                open_batch(device, 'A02', 'B')  # 冻结后槽位锁定

    def test_freeze_requires_sealed_active_batch(self):
        with closing(open_device(self.device_path)) as device:
            register_table(device, 'A', {7: '预热'})
            register_table(device, 'B', {7: '稳态'})
            open_batch(device, 'A01', 'A')
            append_row(device, 'A01', 'P', 7, 11)
            with self.assertRaises(ValueError):
                prepare_turnover(device, 'A')

    def test_freeze_requires_generation_batches(self):
        with closing(open_device(self.device_path)) as device:
            register_table(device, 'A', {7: '预热'})
            with self.assertRaises(ValueError):
                prepare_turnover(device, 'A')
            with self.assertRaises(ValueError):
                prepare_turnover(device, 'absent')

    def test_swap_clears_old_mapping_and_keeps_history(self):
        with closing(open_device(self.device_path)) as device:
            p, r = self.build_generation_a(device)
            payload = export_batch(device, 'A01')  # 办理前固定 V1 整件
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt = accept_turnover(receiver, control)
        with closing(open_device(self.device_path)) as device:
            activate_table(device, 'B', receipt)
            self.assertIsNone(load_table(device, 'A'))
            self.assertEqual(load_table(device, 'B').table_id, 'B')
            self.assertEqual(read_active_table(device).table_id, 'B')
            # 旧表号、已发页及出处继续可查。
            self.assertEqual(load_issued_pages(device, 'A01'), (p, r))
            # 已固定的 V1 整件仍可原样导出。
            self.assertEqual(export_batch(device, 'A01'), payload)
            # 旧映射清除后拒绝为未导出批新制 V1 整件。
            open_batch(device, 'B01', 'B')
            append_row(device, 'B01', 'Q', 7, 11)
            seal_batch(device, 'B01')
            # 退出的表号不可重登取得新含义。
            with self.assertRaises(ValueError):
                register_table(device, 'A', {7: '别的'})
            # 新一代批可正常导出。
            self.assertIsInstance(export_batch(device, 'B01'), bytes)

    def test_retired_table_cannot_open_batch(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt = accept_turnover(receiver, control)
        with closing(open_device(self.device_path)) as device:
            activate_table(device, 'B', receipt)
            with self.assertRaises(ValueError):
                open_batch(device, 'A02', 'A')

    def test_unexported_batch_refuses_new_v1_piece_after_map_clear(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)  # A01 从未导出过 V1 整件
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt = accept_turnover(receiver, control)
        with closing(open_device(self.device_path)) as device:
            activate_table(device, 'B', receipt)
            with self.assertRaises(ValueError):
                export_batch(device, 'A01')
            # 原页仍可领取。
            self.assertEqual([page.page_id for page in load_issued_pages(device, 'A01')],
                             ['P', 'R'])

    def test_late_split_page_after_device_db_closed(self):
        with closing(open_device(self.device_path)) as device:
            p, r = self.build_generation_a(device)
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt = accept_turnover(receiver, control)
        with closing(open_device(self.device_path)) as device:
            activate_table(device, 'B', receipt)
        # 设备库已关闭，接班库独立接纳迟到旧页。
        with closing(open_receiving(self.receiving_path)) as receiver:
            (p_row,) = deliver(receiver, p)
            self.assertEqual(p_row.segment, '预热')
            with self.assertRaises(ValueError):
                publish_report(receiver, 'A01')
            deliver(receiver, r)
            report = publish_report(receiver, 'A01')
            self.assertEqual(report.totals, (('预热', 11), ('检查', 3)))

    def test_swap_rejects_wrong_and_used_receipts(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)
            control_a = prepare_turnover(device, 'A')
            # 第二台设备的另一截面。
            other_path = self.root / 'other-device.sqlite'
            with closing(open_device(other_path)) as other:
                register_table(other, 'A', {7: '预热'})
                register_table(other, 'B', {7: '稳态'})
                open_batch(other, 'X01', 'A')
                append_row(other, 'X01', 'P', 7, 5)
                seal_batch(other, 'X01')
                other_control = prepare_turnover(other, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            good_receipt = accept_turnover(receiver, control_a)
            other_receipt = accept_turnover(
                open_receiving(self.root / 'other-receiving.sqlite'), other_control)
        with closing(open_device(self.device_path)) as device:
            with self.assertRaises(ValueError):
                activate_table(device, 'B', other_receipt)  # 不同截面
            with self.assertRaises(ValueError):
                activate_table(device, 'A', good_receipt)  # 须为另一张表
            activate_table(device, 'B', good_receipt)
            with self.assertRaises(ValueError):
                activate_table(device, 'B', other_receipt)  # 截面已消费
            self.assertEqual(read_active_table(device).table_id, 'B')

    def test_swap_rejects_invalid_receipt_bytes(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)
            prepare_turnover(device, 'A')
            for bad in (b'not json', b'{"version":1}', json.dumps({'version': 2}).encode()):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        activate_table(device, 'B', bad)

    def test_cross_section_same_batch_rejected(self):
        with closing(open_device(self.device_path)) as device:
            p, _ = self.build_generation_a(device)
            control = prepare_turnover(device, 'A')
        other_device = self.root / 'other-device.sqlite'
        with closing(open_device(other_device)) as other:
            register_table(other, 'A', {7: '预热'})
            open_batch(other, 'A01', 'A')
            append_row(other, 'A01', 'P', 7, 5)  # 同批号 A01、同页号 P，毫秒不同
            seal_batch(other, 'A01')
            foreign_pages = load_issued_pages(other, 'A01')
            foreign_control = prepare_turnover(other, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, control)
            with self.assertRaises(ValueError):
                accept_turnover(receiver, foreign_control)
            with self.assertRaises(ValueError):
                deliver(receiver, foreign_pages[0])
            # 原件不受影响。
            (row,) = deliver(receiver, p)
            self.assertEqual(row.milliseconds, 11)

    def test_second_generation_swap_keeps_both_histories(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt_a = accept_turnover(receiver, control)
        with closing(open_device(self.device_path)) as device:
            register_table(device, 'C', {7: '诊断'})
            activate_table(device, 'B', receipt_a)
            open_batch(device, 'B01', 'B')
            append_row(device, 'B01', 'Q', 7, 11)
            seal_batch(device, 'B01')
            control_b = prepare_turnover(device, 'B')
        with closing(open_receiving(self.root / 'gen2-receiving.sqlite')) as receiver:
            receipt_b = accept_turnover(receiver, control_b)
        with closing(open_device(self.device_path)) as device:
            self.assertEqual(activate_table(device, 'C', receipt_b), 'C')
            self.assertIsNone(load_table(device, 'A'))
            self.assertIsNone(load_table(device, 'B'))
            self.assertEqual(load_table(device, 'C').table_id, 'C')
            self.assertEqual([p.page_id for p in load_issued_pages(device, 'A01')],
                             ['P', 'R'])
            self.assertEqual([p.page_id for p in load_issued_pages(device, 'B01')], ['Q'])


class ControlValidationTests(TurnoverTestBase):
    def _control(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)
            return prepare_turnover(device, 'A')

    def test_missing_page_in_control_rejected(self):
        control = json.loads(self._control())
        original_seal = control['seal']
        # 使用原截面标识但省略 R 页：seal 仍绑定完整截面，必须拒绝。
        control['batches'][0]['pages'] = [
            page for page in control['batches'][0]['pages'] if page['page_id'] != 'R'
        ]
        control['seal'] = original_seal
        with closing(open_receiving(self.receiving_path)) as receiver:
            with self.assertRaises(ValueError):
                accept_turnover(receiver, json.dumps(control).encode())

    def test_tampered_seal_and_evidence_rejected(self):
        raw = self._control()
        control = json.loads(raw)
        tampered = dict(control, seal='0' * 64)
        with closing(open_receiving(self.receiving_path)) as receiver:
            with self.assertRaises(ValueError):
                accept_turnover(receiver, json.dumps(tampered).encode())
        changed = json.loads(raw)
        changed['evidence']['entries'][0][1] = '稳态'
        with closing(open_receiving(self.receiving_path)) as receiver:
            with self.assertRaises(ValueError):
                accept_turnover(receiver, json.dumps(changed).encode())
        wrong_version = json.loads(raw)
        wrong_version['version'] = 1
        with closing(open_receiving(self.receiving_path)) as receiver:
            with self.assertRaises(ValueError):
                accept_turnover(receiver, json.dumps(wrong_version).encode())

    def test_same_generation_different_content_rejected(self):
        first = self._control()
        second_doc = json.loads(first)
        # 同一表代但目录摘要被改成异件。
        second_doc['batches'][0]['pages'][0]['body_digest'] = 'a' * 64
        from exchange.turnover import canonical_json
        second_doc['seal'] = digest_bytes(canonical_json({
            'table_id': second_doc['table_id'],
            'batches': second_doc['batches'],
            'evidence': second_doc['evidence'],
        }))
        second = json.dumps(second_doc).encode()
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, first)
            with self.assertRaises(ValueError):
                accept_turnover(receiver, second)
            self.assertEqual(accept_turnover(receiver, first),
                             accept_turnover(receiver, first))

    def test_commit_failure_leaves_no_partial_takeover(self):
        control = self._control()
        receiver = open_receiving(self.receiving_path)
        receiver.execute('''
            CREATE TEMP TRIGGER stop_turnover BEFORE INSERT ON turnover_codes
            BEGIN SELECT RAISE(ABORT, 'turnover interrupted'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'turnover interrupted'):
            accept_turnover(receiver, control)
        receiver.close()
        with closing(open_receiving(self.receiving_path)) as reopened:
            self.assertEqual(reopened.execute('SELECT count(*) FROM turnovers').fetchone(), (0,))
            self.assertEqual(reopened.execute('SELECT count(*) FROM turnover_batches').fetchone(), (0,))
            self.assertEqual(reopened.execute('SELECT count(*) FROM batch_directories').fetchone(), (0,))
            # 故障去除后重投成功，只有成功接管的回执可换表。
            receipt = accept_turnover(reopened, control)
            self.assertIsInstance(receipt, bytes)
        with closing(open_device(self.device_path)) as device:
            self.assertEqual(activate_table(device, 'B', receipt), 'B')


class PageIntakeTests(TurnoverTestBase):
    def setUp(self):
        super().setUp()
        with closing(open_device(self.device_path)) as device:
            self.p, self.r = self.build_generation_a(device)
            self.control = prepare_turnover(device, 'A')

    def test_page_before_takeover_staged_then_promoted(self):
        with closing(open_receiving(self.receiving_path)) as receiver:
            self.assertIsNone(deliver(receiver, self.p))
            self.assertEqual(receiver.execute('SELECT count(*) FROM staged_pages').fetchone(), (1,))
            # 暂存重投幂等。
            self.assertIsNone(deliver(receiver, self.p))
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            # 接管后暂存页已提升，缺 R 仍不可签账。
            self.assertEqual(receiver.execute('SELECT count(*) FROM staged_pages').fetchone(), (0,))
            with self.assertRaises(ValueError):
                publish_report(receiver, 'A01')
            deliver(receiver, self.r)
            report = publish_report(receiver, 'A01')
            self.assertEqual(report.totals, (('预热', 11), ('检查', 3)))

    def test_wrong_generation_page_rejected(self):
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            wrong = IssuedPage('A01', 'B', self.p.sequence, self.p.page_id, self.p.body)
            with self.assertRaises(ValueError):
                deliver(receiver, wrong)

    def test_wrong_sequence_and_digest_rejected(self):
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            bad_seq = IssuedPage('A01', 'A', 9, 'P', self.p.body)
            with self.assertRaises(ValueError):
                deliver(receiver, bad_seq)
            with self.assertRaises(ValueError):
                accept_page(receiver, self.p, 'f' * 64)
            unknown_page = IssuedPage('A01', 'A', 3, 'Z', self.p.body)
            with self.assertRaises(ValueError):
                deliver(receiver, unknown_page)

    def test_same_identity_different_bytes_rejected(self):
        altered_body = self.p.body.replace(b'11', b'12', 1)
        self.assertNotEqual(altered_body, self.p.body)
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            deliver(receiver, self.p)
            altered = IssuedPage('A01', 'A', self.p.sequence, 'P', altered_body)
            with self.assertRaises(ValueError):
                deliver(receiver, altered)
            # 首次结果保持，且没有新增计量。
            (row,) = deliver(receiver, self.p)
            self.assertEqual(row.milliseconds, 11)
            self.assertEqual(receiver.execute('SELECT count(*) FROM pending_pages').fetchone(), (1,))

    def test_staged_conflicting_bytes_rejected_after_takeover(self):
        altered_body = self.p.body.replace(b'11', b'12', 1)
        altered = IssuedPage('A01', 'A', self.p.sequence, 'P', altered_body)
        with closing(open_receiving(self.receiving_path)) as receiver:
            self.assertIsNone(deliver(receiver, altered))
            with self.assertRaises(ValueError):
                accept_turnover(receiver, self.control)
            self.assertEqual(receiver.execute('SELECT count(*) FROM turnovers').fetchone(), (0,))

    def test_illegal_rows_rejected_even_if_digest_self_consistent(self):
        illegal = b'[{"line_no":1,"code":99,"milliseconds":3}]'
        page = IssuedPage('A01', 'A', 1, 'P', illegal)
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            with self.assertRaises(ValueError):
                accept_page(receiver, page, digest_bytes(illegal))

    def test_replay_returns_first_rows_and_counts_once(self):
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            first = deliver(receiver, self.p)
            second = deliver(receiver, self.p)
            self.assertEqual(first, second)
            deliver(receiver, self.r)
            deliver(receiver, self.p)
            deliver(receiver, self.r)
            rows = load_accepted_rows(receiver, 'A01')
            self.assertEqual([(row.page_id, row.line_no) for row in rows],
                             [('P', 1), ('R', 1)])

    def test_equal_body_distinct_pages_both_counted(self):
        device_path = self.root / 'equal-device.sqlite'
        receiver_path = self.root / 'equal-receiving.sqlite'
        with closing(open_device(device_path)) as device:
            register_table(device, 'A', {7: '预热'})
            register_table(device, 'B', {7: '稳态'})
            open_batch(device, 'A10', 'A')
            append_row(device, 'A10', 'P20', 7, 11)
            append_row(device, 'A10', 'P03', 7, 11)
            seal_batch(device, 'A10')
            first, second = load_issued_pages(device, 'A10')
            self.assertEqual(first.body, second.body)
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(receiver_path)) as receiver:
            accept_turnover(receiver, control)
            (row1,) = deliver(receiver, first)
            (row2,) = deliver(receiver, second)
            report = publish_report(receiver, 'A10')
            self.assertEqual(report.totals, (('预热', 22),))
            self.assertEqual([(c.page_id, c.line_no) for c in report.contributions],
                             [('P20', 1), ('P03', 1)])
            self.assertEqual({row1.page_id, row2.page_id}, {'P20', 'P03'})

    def test_empty_batch_completes_on_takeover(self):
        empty_device = self.root / 'empty-device.sqlite'
        empty_receiver = self.root / 'empty-receiving.sqlite'
        with closing(open_device(empty_device)) as device:
            register_table(device, 'C', {1: '空载'})
            open_batch(device, 'C01', 'C')
            seal_batch(device, 'C01')
            control = prepare_turnover(device, 'C')
        with closing(open_receiving(empty_receiver)) as receiver:
            accept_turnover(receiver, control)
            report = publish_report(receiver, 'C01')
            self.assertEqual(report.totals, ())
            self.assertEqual(report.contributions, ())


class MixedIntakeTests(TurnoverTestBase):
    def setUp(self):
        super().setUp()
        with closing(open_device(self.device_path)) as device:
            self.p, self.r = self.build_generation_a(device)
            self.payload = export_batch(device, 'A01')
            self.control = prepare_turnover(device, 'A')

    def test_whole_piece_first_keeps_receipt_and_account_on_split_replay(self):
        payload, control = self.payload, self.control
        receiver_a = open_receiving(self.root / 'mix-a.sqlite')
        receive_batch(receiver_a, payload)
        whole_receipt = find_receipt(receiver_a, 'A01')
        whole_report = publish_report(receiver_a, 'A01')
        accept_turnover(receiver_a, control)  # 后到控制件须兼容
        (p_row,) = deliver(receiver_a, self.p)  # 分路重投不替换旧账
        self.assertEqual(find_receipt(receiver_a, 'A01'), whole_receipt)
        self.assertEqual(publish_report(receiver_a, 'A01'), whole_report)
        self.assertEqual((p_row.page_id, p_row.segment, p_row.milliseconds),
                         ('P', '预热', 11))
        receiver_a.close()

    def test_split_first_then_whole_piece_completes_same_account(self):
        payload, control = self.payload, self.control
        receiver_b = open_receiving(self.root / 'mix-b.sqlite')
        accept_turnover(receiver_b, control)
        deliver(receiver_b, self.p)  # R 仍扣着
        with self.assertRaises(ValueError):
            publish_report(receiver_b, 'A01')
        # 仅给 P 正文追加空白、长度和摘要同步更新的自洽异件，须拒绝。
        altered_doc = json.loads(payload)
        import base64
        original = base64.b64decode(altered_doc['pages'][0]['body'])
        altered_body = original + b' '
        altered_doc['pages'][0]['body'] = base64.b64encode(altered_body).decode()
        entry = altered_doc['manifest']['pages'][0]
        entry['byte_length'] = len(altered_body)
        entry['body_digest'] = digest_bytes(altered_body)
        altered_payload = json.dumps(altered_doc, sort_keys=True,
                                     separators=(',', ':')).encode()
        with self.assertRaises(ValueError):
            receive_batch(receiver_b, altered_payload)
        with self.assertRaises(ValueError):
            publish_report(receiver_b, 'A01')  # 仍不能签账
        # 真正的旧整件到达，签同一份账。
        receipt = receive_batch(receiver_b, payload)
        report = publish_report(receiver_b, 'A01')
        self.assertEqual(report.totals, (('预热', 11), ('检查', 3)))
        # 重投 R 与整件均不多计。
        deliver(receiver_b, self.r)
        self.assertEqual(receive_batch(receiver_b, payload), receipt)
        self.assertEqual(load_accepted_rows(receiver_b, 'A01'),
                         load_accepted_rows(receiver_b, 'A01'))
        self.assertEqual(len(load_accepted_rows(receiver_b, 'A01')), 2)
        receiver_b.close()

    def test_foreign_whole_piece_after_takeover_rejected(self):
        payload, control = self.payload, self.control
        doc = json.loads(payload)  # 同批、同页数但原字节不同（数值与工段相同仍为异件）。
        import base64
        original = base64.b64decode(doc['pages'][0]['body'])
        reordered = b'[{"code":7,"line_no":1,"milliseconds":11}]'
        self.assertNotEqual(reordered, original)
        doc['pages'][0]['body'] = base64.b64encode(reordered).decode()
        doc['manifest']['pages'][0]['byte_length'] = len(reordered)
        doc['manifest']['pages'][0]['body_digest'] = digest_bytes(reordered)
        foreign = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, control)
            with self.assertRaises(ValueError):
                receive_batch(receiver, foreign)

    def test_two_routes_share_normalized_content_identity(self):
        # 同一原件经 V1 整件与 V2 分路，必须得到同一 content_id 与回执编号。
        with closing(open_receiving(self.root / 'route-v1.sqlite')) as receiver:
            v1_receipt = receive_batch(receiver, self.payload)
        with closing(open_receiving(self.root / 'route-v2.sqlite')) as receiver:
            accept_turnover(receiver, self.control)
            for page in (self.p, self.r):
                accept_page(receiver, page, digest_bytes(page.body))
            v2_receipt = find_receipt(receiver, 'A01')
        self.assertEqual(v1_receipt.content_id, v2_receipt.content_id)
        self.assertEqual(v1_receipt.receipt_id, v2_receipt.receipt_id)

    def test_whole_piece_after_split_completion_replays_same_receipt(self):
        with closing(open_receiving(self.receiving_path)) as receiver:
            accept_turnover(receiver, self.control)
            for page in (self.p, self.r):
                accept_page(receiver, page, digest_bytes(page.body))
            split_receipt = find_receipt(receiver, 'A01')
            whole_receipt = receive_batch(receiver, self.payload)
            self.assertEqual(whole_receipt, split_receipt)
            self.assertEqual(len(load_accepted_rows(receiver, 'A01')), 2)


class ReopenTests(TurnoverTestBase):
    def test_reopen_receiver_refetches_receipt_and_rows(self):
        with closing(open_device(self.device_path)) as device:
            p, r = self.build_generation_a(device)
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt = accept_turnover(receiver, control)
        with closing(open_receiving(self.receiving_path)) as receiver:
            self.assertEqual(accept_turnover(receiver, control), receipt)
            deliver(receiver, p)
            deliver(receiver, r)
            report = publish_report(receiver, 'A01')
            report_id = report.report_id
        with closing(open_receiving(self.receiving_path)) as receiver:
            reopened = read_report(receiver, report_id)
            self.assertEqual(reopened.totals, (('预热', 11), ('检查', 3)))
            self.assertEqual([(c.page_id, c.line_no) for c in reopened.contributions],
                             [('P', 1), ('R', 1)])

    def test_reopen_device_completes_swap_then_continue(self):
        with closing(open_device(self.device_path)) as device:
            self.build_generation_a(device)
            control = prepare_turnover(device, 'A')
        with closing(open_receiving(self.receiving_path)) as receiver:
            receipt = accept_turnover(receiver, control)
        with closing(open_device(self.device_path)) as device:
            activate_table(device, 'B', receipt)
        with closing(open_device(self.device_path)) as device:
            self.assertIsNone(load_table(device, 'A'))
            open_batch(device, 'B01', 'B')
            append_row(device, 'B01', 'Q', 7, 11)
            seal_batch(device, 'B01')
            (q,) = load_issued_pages(device, 'B01')
        self.assertEqual(q.page_id, 'Q')


if __name__ == '__main__':
    unittest.main()
