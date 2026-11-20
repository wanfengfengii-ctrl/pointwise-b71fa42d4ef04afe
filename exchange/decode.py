from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass

from device.issue import IssuedPage
from device.page_body import decode_page_body, load_json_bytes
from device.pages import SourcePage
from exchange.encode import encode_exchange
from exchange.integrity import digest_bytes
from exchange.manifest import Manifest, PageEntry, verify_manifest
from tables.catalog import CodeTable


@dataclass(frozen=True, slots=True)
class DecodedExchange:
    manifest: Manifest
    table: CodeTable
    pages: tuple[SourcePage, ...]
    content_id: str


def _object(value, fields: set[str], label: str) -> dict:
    if type(value) is not dict or value.keys() != fields:
        raise ValueError(f'无效的{label}字段')
    return value


def _array(value, label: str) -> list:
    if type(value) is not list:
        raise ValueError(f'{label}须为数组')
    return value


def _decode_manifest(value) -> Manifest:
    fields = _object(value, {'batch_id', 'table_id', 'table_digest', 'pages'}, '清册')
    entries = tuple(
        PageEntry(**_object(entry, {'sequence', 'page_id', 'byte_length', 'body_digest'}, '清册页'))
        for entry in _array(fields['pages'], '清册页')
    )
    return Manifest(fields['batch_id'], fields['table_id'], fields['table_digest'], entries)


def _decode_issued_page(value) -> IssuedPage:
    page = _object(value, {'batch_id', 'table_id', 'sequence', 'page_id', 'body'}, '签发页')
    if type(page['body']) is not str:
        raise ValueError('原页正文须为 Base64 文本')
    try:
        body = b64decode(page['body'], validate=True)
    except (Base64Error, ValueError) as error:
        raise ValueError('无效的 Base64 原页正文') from error
    return IssuedPage(page['batch_id'], page['table_id'], page['sequence'], page['page_id'], body)


def decode_exchange(payload: bytes) -> DecodedExchange:
    doc = _object(load_json_bytes(payload), {'version', 'manifest', 'table', 'pages'}, '交换件')
    if type(doc['version']) is not int or doc['version'] != 1:
        raise ValueError('仅接纳 V1 整批交换件')
    manifest = _decode_manifest(doc['manifest'])
    table_doc = _object(doc['table'], {'table_id', 'entries'}, '固定表')
    table = CodeTable(table_doc['table_id'], tuple(_array(table_doc['entries'], '固定表条目')))
    pages = tuple(_decode_issued_page(page) for page in _array(doc['pages'], '签发页'))
    verify_manifest(manifest, table, pages)
    parsed = tuple(SourcePage(page.sequence, page.page_id, decode_page_body(page.body)) for page in pages)
    content_id = digest_bytes(encode_exchange(manifest, table, pages))
    return DecodedExchange(manifest, table, parsed, content_id)
