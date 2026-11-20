"""Version 2 turnover control pieces and takeover receipts.

The control piece freezes one code-table generation: every issued batch of
that generation, each batch's full original-page directory and the fixed
interpretation evidence.  The receipt is the receiver's self-contained
takeover proof.  Both travel as standalone UTF-8 JSON bytes.
"""
from dataclasses import dataclass
import json
import re

from exchange.integrity import digest_bytes
from exchange.manifest import encode_fixed_table
from tables.catalog import CodeTable

TURNOVER_VERSION = 2
_DIGEST_RE = re.compile(r'[0-9a-f]{64}')


def canonical_json(document) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


@dataclass(frozen=True, slots=True)
class TurnoverPage:
    sequence: int
    page_id: str
    byte_length: int
    body_digest: str


@dataclass(frozen=True, slots=True)
class TurnoverBatch:
    batch_id: str
    pages: tuple[TurnoverPage, ...]


@dataclass(frozen=True, slots=True)
class ControlEvidence:
    table: CodeTable
    table_digest: str


@dataclass(frozen=True, slots=True)
class TurnoverControl:
    table_id: str
    seal: str
    batches: tuple[TurnoverBatch, ...]
    evidence: ControlEvidence


@dataclass(frozen=True, slots=True)
class TurnoverReceipt:
    seal: str
    table_id: str
    table_digest: str
    batch_ids: tuple[str, ...]
    receipt_id: str


def _require_object(value, label: str) -> dict:
    if type(value) is not dict:
        raise ValueError(f'{label}须为 JSON 对象')
    return value


def _require_text(value, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f'{label}须为非空文本')
    return value


def _require_positive_int(value, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f'{label}须为正整数')
    return value


def _require_digest(value, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f'{label}须为 SHA-256 十六进制摘要')
    return value


def build_control(table: CodeTable, batches: tuple) -> bytes:
    """Freeze the batches of one generation into standalone control bytes.

    ``batches`` are any objects exposing ``batch_id``, ``table_id`` and a
    sealed ``directory`` of page entries (``DeviceBatch`` in this project).
    """
    batch_documents = []
    seen_batches: set[str] = set()
    for batch in batches:
        directory = batch.directory
        if batch.table_id != table.table_id or directory is None:
            raise ValueError('冻结截面只含本代表的已封定批')
        if batch.batch_id in seen_batches:
            raise ValueError('截面内批号重复')
        seen_batches.add(batch.batch_id)
        page_documents = [
            {
                'sequence': entry.sequence,
                'page_id': entry.page_id,
                'byte_length': entry.byte_length,
                'body_digest': entry.body_digest,
            }
            for entry in directory
        ]
        batch_documents.append({'batch_id': batch.batch_id, 'pages': page_documents})
    table_digest = digest_bytes(encode_fixed_table(table))
    evidence = {
        'table_id': table.table_id,
        'table_digest': table_digest,
        'entries': [list(entry) for entry in table.entries],
    }
    seal_base = canonical_json(
        {'table_id': table.table_id, 'batches': batch_documents, 'evidence': evidence}
    )
    seal = digest_bytes(seal_base)
    return canonical_json({
        'version': TURNOVER_VERSION,
        'table_id': table.table_id,
        'seal': seal,
        'batches': batch_documents,
        'evidence': evidence,
    })


def parse_control(data: bytes) -> TurnoverControl:
    if type(data) is not bytes:
        raise ValueError('控制件须为 UTF-8 字节')
    try:
        document = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('控制件须为有效的 UTF-8 JSON') from error
    doc = _require_object(document, '控制件')
    if doc.keys() != {'version', 'table_id', 'seal', 'batches', 'evidence'}:
        raise ValueError('控制件外层字段不符')
    if type(doc['version']) is not int or doc['version'] != TURNOVER_VERSION:
        raise ValueError('仅接纳 version 为 2 的换表控制件')
    table_id = _require_text(doc['table_id'], '控制件表号')
    seal = _require_digest(doc['seal'], '截面标识')

    raw_batches = doc['batches']
    if type(raw_batches) is not list or not raw_batches:
        raise ValueError('控制件须至少含一个批描述')
    batches = []
    seen_batches: set[str] = set()
    seen_pages: set[str] = set()
    for batch_doc in raw_batches:
        batch_obj = _require_object(batch_doc, '批描述')
        if batch_obj.keys() != {'batch_id', 'pages'}:
            raise ValueError('批描述字段不符')
        batch_id = _require_text(batch_obj['batch_id'], '批号')
        if batch_id in seen_batches:
            raise ValueError('控制件批号重复')
        seen_batches.add(batch_id)
        raw_pages = batch_obj['pages']
        if type(raw_pages) is not list:
            raise ValueError('页目录须为数组')
        pages = []
        for position, page_doc in enumerate(raw_pages, 1):
            page_obj = _require_object(page_doc, '页目录')
            if page_obj.keys() != {'sequence', 'page_id', 'byte_length', 'body_digest'}:
                raise ValueError('页目录字段不符')
            sequence = _require_positive_int(page_obj['sequence'], '发页序')
            if sequence != position:
                raise ValueError('页目录发页序不连续')
            page_id = _require_text(page_obj['page_id'], '页号')
            if page_id in seen_pages:
                raise ValueError('控制件页号重复')
            seen_pages.add(page_id)
            byte_length = _require_positive_int(page_obj['byte_length'], '正文长度')
            body_digest = _require_digest(page_obj['body_digest'], '正文摘要')
            pages.append(TurnoverPage(sequence, page_id, byte_length, body_digest))
        batches.append(TurnoverBatch(batch_id, tuple(pages)))

    evidence_obj = _require_object(doc['evidence'], '解释依据')
    if evidence_obj.keys() != {'table_id', 'table_digest', 'entries'}:
        raise ValueError('解释依据字段不符')
    evidence_table_id = _require_text(evidence_obj['table_id'], '依据表号')
    if evidence_table_id != table_id:
        raise ValueError('解释依据与截面表号错绑')
    table_digest = _require_digest(evidence_obj['table_digest'], '码表摘要')
    raw_entries = evidence_obj['entries']
    if type(raw_entries) is not list or not raw_entries:
        raise ValueError('解释依据须含码表条目')
    entries = []
    for pair in raw_entries:
        if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not int:
            raise ValueError('码表依据条目须为整数代码与工段文本')
        entries.append((pair[0], _require_text(pair[1], '工段名')))
    table = CodeTable(table_id, tuple(entries))
    if digest_bytes(encode_fixed_table(table)) != table_digest:
        raise ValueError('解释依据摘要与码表内容不符')

    rebuilt = build_control(table, tuple(
        _FrozenBatch(batch.batch_id, table_id, batch.pages) for batch in batches
    ))
    if json.loads(rebuilt.decode('utf-8'))['seal'] != seal:
        raise ValueError('截面标识与冻结内容不绑定')
    return TurnoverControl(table_id, seal, tuple(batches), ControlEvidence(table, table_digest))


class _FrozenBatch:
    __slots__ = ('batch_id', 'table_id', 'directory')

    def __init__(self, batch_id, table_id, directory):
        self.batch_id = batch_id
        self.table_id = table_id
        self.directory = directory


def build_receipt(control: TurnoverControl) -> bytes:
    batch_ids = [batch.batch_id for batch in control.batches]
    payload = {
        'version': TURNOVER_VERSION,
        'seal': control.seal,
        'table_id': control.table_id,
        'table_digest': control.evidence.table_digest,
        'batch_ids': batch_ids,
    }
    receipt_id = 'turnover-receipt-' + digest_bytes(canonical_json(payload))
    payload['receipt_id'] = receipt_id
    return canonical_json(payload)


def parse_receipt(data: bytes) -> TurnoverReceipt:
    if type(data) is not bytes:
        raise ValueError('回执须为 UTF-8 字节')
    try:
        document = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('回执须为有效的 UTF-8 JSON') from error
    doc = _require_object(document, '回执')
    if doc.keys() != {'version', 'seal', 'table_id', 'table_digest', 'batch_ids', 'receipt_id'}:
        raise ValueError('回执字段不符')
    if type(doc['version']) is not int or doc['version'] != TURNOVER_VERSION:
        raise ValueError('仅接纳 version 为 2 的换表回执')
    seal = _require_digest(doc['seal'], '回执截面标识')
    table_id = _require_text(doc['table_id'], '回执表号')
    table_digest = _require_digest(doc['table_digest'], '回执码表摘要')
    raw_batch_ids = doc['batch_ids']
    if type(raw_batch_ids) is not list or not raw_batch_ids:
        raise ValueError('回执须含接管批号')
    batch_ids = []
    seen: set[str] = set()
    for batch_id in raw_batch_ids:
        text = _require_text(batch_id, '回执批号')
        if text in seen:
            raise ValueError('回执批号重复')
        seen.add(text)
        batch_ids.append(text)
    receipt_id = _require_text(doc['receipt_id'], '回执编号')
    payload = {
        'version': TURNOVER_VERSION,
        'seal': seal,
        'table_id': table_id,
        'table_digest': table_digest,
        'batch_ids': batch_ids,
    }
    derived = 'turnover-receipt-' + digest_bytes(canonical_json(payload))
    if receipt_id != derived:
        raise ValueError('回执编号与载荷不符')
    return TurnoverReceipt(seal, table_id, table_digest, tuple(batch_ids), receipt_id)
