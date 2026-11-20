from exchange.decode import DecodedExchange
from receiving.rows import AcceptedRow
from tables.resolve import resolve_code


def translate_batch(exchange: DecodedExchange) -> tuple[AcceptedRow, ...]:
    manifest = exchange.manifest
    rows = []
    for page in exchange.pages:
        for row in page.rows:
            segment = resolve_code(exchange.table, row.code)
            rows.append(AcceptedRow(
                manifest.batch_id, manifest.table_id, page.sequence, page.page_id,
                row.line_no, row.code, segment, row.milliseconds,
            ))
    return tuple(rows)
