# 东岬试验中心代码页交换 V1

白班资料员把试验记录器给出的代码和有效毫秒录入设备库。封定后，夜班只需取得完整交换件即可在独立接班库实收。复盘员凭成果号领取各工段的有效时长和原行出处。

## 运行

使用 Python 3.11 标准库。控制台物化项目时提供 `check_project.py`。在项目根目录运行完整回归和正常样例。

```sh
python3 -B check_project.py
python3 -B demo.py
```

也可直接发现完整 unittest 套件。

```sh
python3 -B -m unittest discover -s tests -t . -v
```

样例使用两个独立临时数据库。白班将代码 7 对应到「预热」，依次写入 P20 的 11、P03 的 3 和 P20 的 0 毫秒。封定目录为 P20、P03。夜班收到整件后发布「预热」14 毫秒的账，贡献依次为 P20 第 1 行、P20 第 2 行、P03 第 1 行。次批以另一张表记录「稳态」11 毫秒。样例重开接班库并核对两份账及各自出处。

## Python 接口

通过 `storage.open_device(path)` 和 `storage.open_receiving(path)` 打开各自的 SQLite 文件。初始化可重复进行。调用方负责关闭连接，可使用 `contextlib.closing`。下列写入入口各自管理事务，调用时连接应无未提交事务。

| 模块与入口 | 结果 |
| --- | --- |
| `tables.register.register_table(db, table_id, entries)` | 固定表号。支持映射或键值对序列。相同映射可重登，同号异义拒绝。 |
| `device.batches.open_batch(db, batch_id, table_id)` | 建立绑定所选固定表的活动批。上一活动批须已封定并签发。 |
| `device.record.append_row(db, batch_id, page_id, code, milliseconds)` | 返回从 1 起算的页内原行位。首条有效行确定页序。 |
| `device.seal.seal_batch(db, batch_id)` | 返回带有固定目录的 `DeviceBatch`。可重封领取同一目录。 |
| `exchange.export.export_batch(db, batch_id)` | 返回封定批的固定交换字节。 |
| `receiving.accept.receive_batch(db, payload)` | 完整件实收后返回 `Receipt`。同批同件重投复领，同批异件拒绝。 |
| `reporting.publish.publish_report(db, batch_id)` | 给已实收批发布并返回 `SegmentReport`。已发布的同批复领固定成果。 |
| `reporting.read.read_report(db, report_id)` | 领取固定总额和逐行出处。未知成果号抛 `KeyError`。 |

业务输入或状态不合规时抛 `ValueError`。码表代码和行代码须为整数。毫秒须为非负整数，实测零也是原行。空值、布尔值、浮点数和数字字符串不作为整数输入。工段使用登记原文，整数持久化不经过浮点转换。封后不能追加，活动批不能导出。历史表保留，新批选表不影响已发批。

整件以 UTF-8 JSON 承载版本、封定清册、完整固定表和按发页序排列的 Base64 正文。清册固定表摘要及每页长度和原字节摘要。页外层标识包括批、表、页号及发页序，页内正文保存原行位、代码和毫秒。接班库核对齐页及实际收到的原字节，随后按随件表译码。刷新 JSON 外层空白不会改变内容身份。明确的空目录及空正文集合可形成空账。

实收回执与译码行一并提交。报告的成果号、工段合计与逐行贡献一并提交。写入失败由 SQLite 回滚，允许重试。工段按首次贡献排序，逐行贡献按发页序和页内原序排列。不同原页即使正文相同也分别计量。设备和接班库均可关闭重开，已发件、实收和历史成果保持固定。
