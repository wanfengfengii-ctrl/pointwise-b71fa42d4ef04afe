from dataclasses import dataclass
import json

from device.batches import DeviceBatch
from device.issue import IssuedPage
from exchange.integrity import digest_bytes, verify_body_bytes
from storage import integer_text
from tables.catalog import CodeTable
from tables.validate import require_text


@dataclass(frozen=True, slots=True)
class PageEntry:
    sequence: int
    page_id: str
    byte_length: int
    body_digest: str


@dataclass(frozen=True, slots=True)
class Manifest:
    batch_id: str
    table_id: str
    table_digest: str
    pages: tuple[PageEntry, ...]


def encode_fixed_table(table: CodeTable) -> bytes:
    table_id = json.dumps(table.table_id, ensure_ascii=False)
    pairs = (
        '[' + integer_text(code) + ',' + json.dumps(segment, ensure_ascii=False) + ']'
        for code, segment in table.entries
    )
    return ('{"table_id":' + table_id + ',"entries":[' + ','.join(pairs) + ']}').encode('utf-8')


def build_manifest(
    batch: DeviceBatch, table: CodeTable, pages: tuple[IssuedPage, ...],
) -> Manifest:
    if batch.directory is None:
        raise ValueError('未封定的批不能生成整件')
    manifest = Manifest(
        batch.batch_id, batch.table_id, digest_bytes(encode_fixed_table(table)),
        tuple(PageEntry(entry.sequence, entry.page_id, entry.byte_length, entry.body_digest)
              for entry in batch.directory),
    )
    verify_manifest(manifest, table, pages)
    return manifest


def verify_manifest(manifest: Manifest, table: CodeTable, pages: tuple[IssuedPage, ...]) -> None:
    require_text(manifest.batch_id, '清册批号')
    require_text(manifest.table_id, '清册表号')
    if table.table_id != manifest.table_id or digest_bytes(encode_fixed_table(table)) != manifest.table_digest:
        raise ValueError('码表内容与清册绑定不符')
    if len(manifest.pages) != len(pages):
        raise ValueError('清册和正文页数不符')

    seen: set[str] = set()
    for position, (entry, page) in enumerate(zip(manifest.pages, pages, strict=True), 1):
        require_text(entry.page_id, '清册页号')
        if type(entry.sequence) is not int or entry.sequence != position or entry.page_id in seen:
            raise ValueError('清册页序或页号不唯一')
        if (page.batch_id != manifest.batch_id or page.table_id != manifest.table_id
                or page.page_id != entry.page_id or type(page.sequence) is not int
                or page.sequence != entry.sequence):
            raise ValueError('正文未一一对应清册原页')
        verify_body_bytes(page.body, entry.byte_length, entry.body_digest)
        seen.add(entry.page_id)
