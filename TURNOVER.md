# 东岬试验台换表交班

同一个设备槽从 A 切到 B 后，代码 7 可以从「预热」变成「稳态」。旧页即使在切换以后才到达，也按旧义计量。白班交出一代的解释责任，夜班接管后可离开设备库接收正文。README.md 记载现有整件流程及接口。

## 设备端

- `device.issue.prepare_turnover(db, table_id) -> bytes`。本题中的代号沿用唯一登记的 `table_id`。对活动槽的表办理，须先封定本地活动批。在设备库内冻结该代所有已发批和每批的完整原页目录，返回可独立传送的控制件。冻结后该代不可再开批发页。待换表期间重取同一截面得到相同控制件。原页正文仍可沿 `device.issue.load_issued_pages(db, batch_id)` 提取。
- `device.active_table.activate_table(db, new_table, receipt) -> str`。`new_table` 为另一张已登记、未退出的表号。仅凭绑定当前冻结截面且由接班库成功接管所返的回执切换，返回新表号。切换和旧码表映射清除一并提交。失败保持原槽及可重试资格。不同截面、已使用及无效的回执均拒绝。
- 从设备开始办理换表交班起，`open_batch` 遵守当前槽及冻结状态。后续换代仍走 `activate_table`。退出的表号不可通过重登取得新含义。旧表号、已发页及其出处继续可查，旧代码映射从设备的码表登记清除。办理前已固定的 V1 整件仍可原样导出。已经退出的代只为未导出批提供原页领取，旧映射清除后拒绝为这类批新制 V1 整件。普通 V1 开批、封定和导出流程按 README.md 使用。

## 接班端

- `receiving.accept.accept_turnover(db, control) -> bytes`。`control` 为设备发出的控制件。校验固定截面及解释依据，持久接管后返回可独立传回的回执。同一件在接班库重开后可精确复取原回执。截面不全、错绑、同代异件或与已实收原件冲突时抛 `ValueError`。提交失败无有效回执，也无本次部分接管状态。
- `receiving.accept.accept_page(db, page, body_digest) -> tuple[AcceptedRow, ...] | None`。`page` 是 `device.issue.IssuedPage`，含批号、表代、发页序、页号和未改动的 `bytes` 正文。`body_digest` 是该原正文 SHA-256 的十六进制文本。行格式沿用 V1，原行位从 1 起算，代码和毫秒使用原整数规则。未取得接管依据时可暂存，返回 `None` 表示待接管，调用方待控制件提交后重投。只有控制件覆盖的准确原页才可正式接纳。依发页时的含义返回 `receiving.rows.AcceptedRow`，按页内原序给出完整的原行及工段，实测零保留。合法重投返回相同首次结果。同批错代、已知截面之外的批或页、错发页序、摘要或字节不符、非法行以及同身份异内容均抛 `ValueError`。尚无对应接管信息且无法判断所属的页可以待接管。
- 批内各原页齐备后，该批才取得正式实收资格。正式齐批所需的实收身份和原行同次提交。空批以接管清册明确的空目录取得齐批资格。`publish_report` 和 `read_report` 沿原入口交付固定工段账和逐行出处。贡献顺序按发页序和页内原序。同字节的不同原页分别实收。

## 两种投递入口的共同口径

已经发出的 V1 整件可以在同批分路交班前后到达。接管已提交而批尚未齐备时，两入口仍须遵守同一原件身份。接班库已有同一原件的正式实收时，兼容后到的控制件或分路重投，不替换旧回执或旧账。接管后收到同一原件的 V1 整件，可以凭完整件齐批。整件的同件判定沿用 V1 对原字节、原页绑定和固定表的规范化内容身份。逐行数值与工段相同、原字节不同的整件仍是异件。已经有接管或实收绑定时，异件拒绝并保留先前成果。

控制件使用 UTF-8 JSON，外层给出整数 `version` 值 2、`table_id`、`seal`、`batches` 和 `evidence`。`seal` 是设备冻结的截面标识，绑定所有批、目录和解释依据。`batches` 是批描述数组，每项有 `batch_id` 和 `pages`，页目录按发页序列出 `sequence`、`page_id`、`byte_length` 和 `body_digest`。允许为口径核验携带其他必要信息。解释依据及回执内部表示由实现决定。控制件可脱离设备独立接纳，正文可以全部留在设备或运输途中。使用原截面标识但省略一批或一页的控制件拒绝。设备、接班库和回执传递端可信，核对传输损坏和身份混用。范围限于单台设备与一个接班库。

新入口使用和 V1 相同的调用约定。调用方传入无未提交事务的连接，非法业务输入或状态抛 `ValueError`。由于提交失败而未取得结果的请求可以重投。保留原接口、旧成果及全部原回归。

## 公开样例

A 映射 7 为预热、8 为检查。A01 的 P、R 依次是 `(7, 11)`、`(8, 3)`，其中 R 的正文一直扣到 P 实收以后。B 映射 7 为稳态。B01 的 Q 与 P 原字节相同。下面的调用展示完成后的主链，可用作演示夹具。

```python
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from device.active_table import activate_table
from device.batches import open_batch
from device.issue import load_issued_pages, prepare_turnover
from device.record import append_row
from device.seal import seal_batch
from exchange.integrity import digest_bytes
from receiving.accept import accept_page, accept_turnover
from reporting.publish import publish_report
from reporting.read import read_report
from storage import open_device, open_receiving
from tables.register import register_table


def deliver(db, page):
    return accept_page(db, page, digest_bytes(page.body))


with TemporaryDirectory() as directory:
    device_path = Path(directory) / 'device.sqlite'
    receiving_path = Path(directory) / 'receiving.sqlite'
    with closing(open_device(device_path)) as device:
        register_table(device, 'A', {7: '预热', 8: '检查'})
        register_table(device, 'B', {7: '稳态'})
        open_batch(device, 'A01', 'A')
        append_row(device, 'A01', 'P', 7, 11)
        append_row(device, 'A01', 'R', 8, 3)
        seal_batch(device, 'A01')
        p, r = load_issued_pages(device, 'A01')
        control_a = prepare_turnover(device, 'A')

    with closing(open_receiving(receiving_path)) as receiver:
        accept_turnover(receiver, control_a)  # 模拟成功答复丢失。
    with closing(open_receiving(receiving_path)) as receiver:
        receipt_a = accept_turnover(receiver, control_a)
        assert accept_turnover(receiver, control_a) == receipt_a

    with closing(open_device(device_path)) as device:
        assert activate_table(device, 'B', receipt_a) == 'B'
        open_batch(device, 'B01', 'B')
        append_row(device, 'B01', 'Q', 7, 11)
        seal_batch(device, 'B01')
        (q,) = load_issued_pages(device, 'B01')
        control_b = prepare_turnover(device, 'B')

    assert p.body == q.body
    with closing(open_receiving(receiving_path)) as receiver:
        assert deliver(receiver, q) is None
        accept_turnover(receiver, control_b)
        (new_row,) = deliver(receiver, q)
        (old_row,) = deliver(receiver, p)
        assert (old_row.page_id, old_row.segment, old_row.milliseconds) == ('P', '预热', 11)
        assert (new_row.page_id, new_row.segment, new_row.milliseconds) == ('Q', '稳态', 11)
        try:
            publish_report(receiver, 'A01')
        except ValueError:
            pass
        else:
            raise AssertionError('R 未到，A01 不具备签账资格')
        deliver(receiver, r)
        assert deliver(receiver, p) == (old_row,)
        report_a = publish_report(receiver, 'A01')
        report_b = publish_report(receiver, 'B01')

    with closing(open_receiving(receiving_path)) as receiver:
        old = read_report(receiver, report_a.report_id)
        new = read_report(receiver, report_b.report_id)
        assert old.totals == (('预热', 11), ('检查', 3))
        assert new.totals == (('稳态', 11),)
        assert [(row.page_id, row.line_no) for row in old.contributions] == [('P', 1), ('R', 1)]
        assert [(row.page_id, row.line_no) for row in new.contributions] == [('Q', 1)]
        print(old.report_id, old.totals, new.report_id, new.totals)
```

补充核账可沿用同一组记录。保留原 `seal` 去掉控制件中 A01 的 R，或把另一截面的回执交给当前设备，均应拒绝。截面冻结后尝试再次用 A 开批须拒绝。接管提交失败可在测试连接拒绝 SQLite `COMMIT` 来核对，去掉故障后重投，只有成功接管的回执可换表。合法重投和错页错代分别核对行计量一次和无新增计量。样例换表后检查设备登记中的 A 映射已清除，随后关闭设备库再收页。

混投对照在冻结以前另存 A01 的 V1 整件。先在一份接班库实收整件，再接管并重投 P，原回执和原账应保持。另一份接班库先接管并实收 P，仍扣着 R。尝试送入 P 的正文仅增加空白、长度和摘要已随改动更新的自洽 V1 整件，应拒绝且 A01 仍不能签账。送入真正的旧整件后可签同一份 A01 账，再重投 R 和整件均不多计。

继续用 `.pointwise-runtime.json` 运行交付。清单的 `version` 为 1、`kind` 为 `cli`，`setup` 是准备命令的 argv 列表，`verify` 和 `demo` 各是一个 argv。核验须运行全部原回归，并能检查换表、迟到、混投及上面的失败边界，失败使用非零退出码。演示展示实际的接管、擦表、两代工段值及重开领账。附简短说明，写明接管凭据、逐页接纳与正式签账如何交接。
