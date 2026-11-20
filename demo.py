from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from device.batches import open_batch
from device.record import append_row
from device.seal import seal_batch
from exchange.export import export_batch
from receiving.accept import receive_batch
from reporting.publish import publish_report
from reporting.read import read_report
from storage import open_device, open_receiving
from tables.register import register_table


def record_and_export(path, batch_id, table_id, segment, rows):
    with closing(open_device(path)) as device:
        register_table(device, table_id, {7: segment})
        open_batch(device, batch_id, table_id)
        for page, code, milliseconds in rows:
            append_row(device, batch_id, page, code, milliseconds)
        seal_batch(device, batch_id)
        return export_batch(device, batch_id)


def report_values(report):
    return {
        'totals': [[segment, ms] for segment, ms in report.totals],
        'contributions': [
            [row.batch_id, row.table_id, row.sequence, row.page_id, row.line_no,
             row.code, row.segment, row.milliseconds]
            for row in report.contributions
        ],
    }


def main():
    old_rows = [('P20', 7, 11), ('P03', 7, 3), ('P20', 7, 0)]
    new_rows = [('P20', 7, 11)]
    expected_old = {
        'totals': [['预热', 14]],
        'contributions': [
            ['A01', 'A', 1, 'P20', 1, 7, '预热', 11],
            ['A01', 'A', 1, 'P20', 2, 7, '预热', 0],
            ['A01', 'A', 2, 'P03', 1, 7, '预热', 3],
        ],
    }
    expected_new = {
        'totals': [['稳态', 11]],
        'contributions': [['A02', 'B', 1, 'P20', 1, 7, '稳态', 11]],
    }
    with TemporaryDirectory() as directory:
        device_path = Path(directory) / 'device.sqlite'
        receiving_path = Path(directory) / 'receiving.sqlite'
        first = record_and_export(device_path, 'A01', 'A', '预热', old_rows)
        with closing(open_receiving(receiving_path)) as receiver:
            first_receipt = receive_batch(receiver, first)
            if receive_batch(receiver, first) != first_receipt:
                raise RuntimeError('整件重投未复领原回执')
            old_report = publish_report(receiver, 'A01')
        second = record_and_export(device_path, 'A02', 'B', '稳态', new_rows)
        with closing(open_receiving(receiving_path)) as receiver:
            receive_batch(receiver, second)
            new_report = publish_report(receiver, 'A02')
        with closing(open_receiving(receiving_path)) as receiver:
            reopened_old = read_report(receiver, old_report.report_id)
            reopened_new = read_report(receiver, new_report.report_id)

        actual_old = report_values(old_report)
        downstream = [report_values(reopened_old), report_values(reopened_new)]
        if actual_old != expected_old or downstream != [expected_old, expected_new]:
            raise RuntimeError('固定工段账与手算结果或原行位置不符')
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v8',
            'input': {'A01': {'table_id': 'A', 'code_7': '预热', 'rows': old_rows},
                      'A02': {'table_id': 'B', 'code_7': '稳态', 'rows': new_rows}},
            'expected': expected_old,
            'actual': actual_old,
            'downstream_expected': [expected_old, expected_new],
            'downstream_actual': downstream,
            'code_paths': ['device/seal.py', 'exchange/export.py', 'receiving/accept.py',
                           'reporting/publish.py', 'reporting/aggregate.py', 'reporting/read.py'],
            'oracle': '按首见页序手排 P20、P03，预热手加 11+0+3，稳态取次批唯一原行 11，逐行核对固定成果。',
        }, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
