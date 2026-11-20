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

## 换表交班（V2 分路）

同一个设备槽从 A 切到 B 后，代码 7 可以从「预热」变成「稳态」。旧页即使在切换以后才到达，也按旧义计量。V2 把「解释依据的交接」与「原页正文的运输」拆开：设备先冻结截面并发出控制件，夜班在独立接班库持久接管后，正文可沿分路逐页到达。完整流程见 `TURNOVER.md`。

| 模块与入口 | 结果 |
| --- | --- |
| `device.issue.prepare_turnover(db, table_id)` | 封定活动批后冻结该代全部已发批与每页完整目录，返回 version=2 的 UTF-8 JSON 控制件。冻结后该代不可再开批发页，重取同一截面得到相同控制件。 |
| `device.active_table.activate_table(db, new_table, receipt)` | 只凭绑定当前冻结截面、由接班库成功接管所返的回执切换槽位，并在同一事务清除旧码表映射、登记退出表号。失败保持原槽及可重试资格。 |
| `receiving.accept.accept_turnover(db, control)` | 校验截面、目录与解释依据后持久接管，返回可独立传回的回执。重开后重投同件精确复取原回执；截面不全、错绑、同代异件或与已实收原件冲突抛 `ValueError`。 |
| `receiving.accept.accept_page(db, page, body_digest)` | 分路收页。未取得接管依据时可暂存并返回 `None`；接管后按发页时含义返回 `AcceptedRow`。批内原页齐备才正式实收，合法重投复领首次结果。 |

### 交接口径

- **接管凭据**：控制件外层含 `version=2`、`table_id`、`seal`、`batches`、`evidence`；`seal` 绑定所有批、页目录和解释依据。回执编号由回执载荷自身的 SHA-256 派生，设备据此核对截面绑定、是否已使用及是否有效。
- **逐页接纳**：`accept_page` 核对原页身份（批号、表代、发页序、页号）与原字节（长度、SHA-256 摘要），再用接管依据中的固定码表译码；同字节的不同原页分别计量，同身份异字节拒绝。
- **正式签账**：批内各原页齐备（空批凭接管清册的空目录）才取得实收身份与原行的同次提交；`publish_report`、`read_report` 沿用 V1 入口。整件与分路混投时同一原批只实收一次，异件拒绝且保留先前成果，缺页不得签账。
- **旧成果**：办理前已固定的 V1 整件仍可原样导出；旧映射清除后，未导出批只能领取原页，不能新制 V1 整件。退出的表号不可重登取得新含义。
