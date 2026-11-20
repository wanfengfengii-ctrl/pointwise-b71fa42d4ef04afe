from dataclasses import dataclass
import json

from storage import integer_text, restore_integer


@dataclass(frozen=True, slots=True)
class SourceRow:
    line_no: int
    code: int
    milliseconds: int


def validate_measurement(code: int, milliseconds: int) -> None:
    if type(code) is not int:
        raise ValueError('原行代码须为整数')
    if type(milliseconds) is not int or milliseconds < 0:
        raise ValueError('有效毫秒须为非负整数')


def encode_page_body(rows: tuple[SourceRow, ...]) -> bytes:
    if not rows:
        raise ValueError('签发页须有至少一条原行')
    encoded = []
    for position, row in enumerate(rows, 1):
        if type(row.line_no) is not int or row.line_no != position:
            raise ValueError('页内行位不连续')
        validate_measurement(row.code, row.milliseconds)
        encoded.append(
            '{"line_no":' + integer_text(row.line_no)
            + ',"code":' + integer_text(row.code)
            + ',"milliseconds":' + integer_text(row.milliseconds) + '}'
        )
    return ('[' + ','.join(encoded) + ']').encode('ascii')


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 字段重复')
        result[key] = value
    return result


def load_json_bytes(data: bytes):
    if type(data) is not bytes:
        raise ValueError('输入须为 UTF-8 字节')
    try:
        return json.loads(
            data.decode('utf-8'), parse_int=restore_integer,
            object_pairs_hook=_object_without_duplicates,
        )
    except (ValueError, RecursionError) as error:
        raise ValueError('无效的 UTF-8 JSON') from error


def decode_page_body(body: bytes) -> tuple[SourceRow, ...]:
    document = load_json_bytes(body)
    if type(document) is not list or not document:
        raise ValueError('签发页须有至少一条原行')
    rows = []
    for position, item in enumerate(document, 1):
        if type(item) is not dict or item.keys() != {'line_no', 'code', 'milliseconds'}:
            raise ValueError('无效的原行字段')
        line_no = item['line_no']
        if type(line_no) is not int or line_no != position:
            raise ValueError('页内行位不连续')
        code, milliseconds = item['code'], item['milliseconds']
        validate_measurement(code, milliseconds)
        rows.append(SourceRow(line_no, code, milliseconds))
    return tuple(rows)
