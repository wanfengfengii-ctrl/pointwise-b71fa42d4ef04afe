from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from device.active_table import activate_table
from device.batches import open_batch
from device.issue import load_issued_pages, prepare_turnover
from device.record import append_row
from device.seal import seal_batch
from exchange.export import export_batch
from exchange.integrity import digest_bytes
from receiving.accept import accept_page, accept_turnover, receive_batch
from reporting.publish import publish_report
from reporting.read import read_report
from storage import open_device, open_receiving
from tables.catalog import load_table
from tables.register import register_table


def report_values(report):
    return {
        'totals': [[segment, ms] for segment, ms in report.totals],
        'contributions': [
            [row.batch_id, row.table_id, row.sequence, row.page_id, row.line_no,
             row.code, row.segment, row.milliseconds]
            for row in report.contributions
        ],
    }


def v1_whole_piece(directory):
    """V1 整件链：封定、整件实收、工段账与重开复领，保持原接口与成果。"""
    device_path = directory / 'v1-device.sqlite'
    receiving_path = directory / 'v1-receiving.sqlite'
    old_rows = [('P20', 7, 11), ('P03', 7, 3), ('P20', 7, 0)]
    with closing(open_device(device_path)) as device:
        register_table(device, 'A', {7: '预热'})
        open_batch(device, 'A01', 'A')
        for page, code, milliseconds in old_rows:
            append_row(device, 'A01', page, code, milliseconds)
        seal_batch(device, 'A01')
        payload = export_batch(device, 'A01')
    with closing(open_receiving(receiving_path)) as receiver:
        first_receipt = receive_batch(receiver, payload)
        if receive_batch(receiver, payload) != first_receipt:
            raise RuntimeError('整件重投未复领原回执')
        report = publish_report(receiver, 'A01')
        report_id = report.report_id
    with closing(open_receiving(receiving_path)) as receiver:
        reopened = read_report(receiver, report_id)
    expected = {
        'totals': [['预热', 14]],
        'contributions': [
            ['A01', 'A', 1, 'P20', 1, 7, '预热', 11],
            ['A01', 'A', 1, 'P20', 2, 7, '预热', 0],
            ['A01', 'A', 2, 'P03', 1, 7, '预热', 3],
        ],
    }
    actual = report_values(reopened)
    if actual != expected:
        raise RuntimeError('V1 固定工段账与手算结果不符')
    return actual


def v2_turnover(directory):
    """V2 换表交班：截面冻结、接班接管、擦表、分路收页与重开领账。"""
    device_path = directory / 'device.sqlite'
    receiving_path = directory / 'receiving.sqlite'

    # 白班：A 代封批并冻结截面，R 的正文扣发。
    with closing(open_device(device_path)) as device:
        register_table(device, 'A', {7: '预热', 8: '检查'})
        register_table(device, 'B', {7: '稳态'})
        open_batch(device, 'A01', 'A')
        append_row(device, 'A01', 'P', 7, 11)
        append_row(device, 'A01', 'R', 8, 3)
        seal_batch(device, 'A01')
        p, r = load_issued_pages(device, 'A01')
        control_a = prepare_turnover(device, 'A')
        frozen_again = prepare_turnover(device, 'A')
        if frozen_again != control_a:
            raise RuntimeError('同一截面重取控制件不一致')

    # 夜班：独立接班库持久接管；模拟答复丢失后重开复取同一回执。
    with closing(open_receiving(receiving_path)) as receiver:
        accept_turnover(receiver, control_a)
    with closing(open_receiving(receiving_path)) as receiver:
        receipt_a = accept_turnover(receiver, control_a)

    # 凭回执换表：槽位切到 B，A 的旧代码映射一并擦除。
    with closing(open_device(device_path)) as device:
        switched = activate_table(device, 'B', receipt_a)
        erased = load_table(device, 'A') is None
        open_batch(device, 'B01', 'B')
        append_row(device, 'B01', 'Q', 7, 11)
        seal_batch(device, 'B01')
        (q,) = load_issued_pages(device, 'B01')
        control_b = prepare_turnover(device, 'B')

    if not (switched == 'B' and erased and p.body == q.body):
        raise RuntimeError('换表擦表或同字节页夹具不符')

    with closing(open_receiving(receiving_path)) as receiver:
        # B 代正文先到，接管依据尚在路上：暂存并返回 None。
        early = accept_page(receiver, q, digest_bytes(q.body))
        accept_turnover(receiver, control_b)
        (q_row,) = accept_page(receiver, q, digest_bytes(q.body))
        (p_row,) = accept_page(receiver, p, digest_bytes(p.body))
        # P、Q 原字节相同，仍按各自发页时的含义分别计量。
        if (p_row.segment, p_row.milliseconds) != ('预热', 11):
            raise RuntimeError('旧代 P 未按旧义计预热 11 毫秒')
        if (q_row.segment, q_row.milliseconds) != ('稳态', 11):
            raise RuntimeError('新代 Q 未按新义计稳态 11 毫秒')
        # R 未到，缺页不得签账。
        try:
            publish_report(receiver, 'A01')
        except ValueError:
            pass
        else:
            raise RuntimeError('缺页时不应具备签账资格')
        accept_page(receiver, r, digest_bytes(r.body))  # 擦表后迟到的旧页
        report_a = publish_report(receiver, 'A01')
        report_b = publish_report(receiver, 'B01')
        ids = (report_a.report_id, report_b.report_id)

    # 重开接班库，按成果号查回原行。
    with closing(open_receiving(receiving_path)) as receiver:
        old = read_report(receiver, ids[0])
        new = read_report(receiver, ids[1])
    if old.totals != (('预热', 11), ('检查', 3)) or new.totals != (('稳态', 11),):
        raise RuntimeError('两代工段合计与发页时含义不符')
    return {
        'switched_table': switched,
        'old_mapping_erased': erased,
        'same_body_pages': [p_row.page_id, q_row.page_id],
        'old_report': report_values(old),
        'new_report': report_values(new),
        'report_ids': list(ids),
    }


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        v1 = v1_whole_piece(root)
        v2 = v2_turnover(root)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v8',
            'v1_whole_piece': v1,
            'v2_turnover': v2,
            'handoff_notes': (
                '设备先冻结整代截面并发出 version=2 控制件；接班库持久接管全部批目录、'
                '页摘要和解释依据后给回执，设备只凭绑定当前截面的有效回执换表并擦除旧映射。'
                '正文可先到暂存，接管后按原页身份与原字节逐页核对接纳，合法重投复领首次结果；'
                '批内原页齐备才取得实收资格并正式签账，整件与分路同身份只实收一次。'
            ),
        }, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
