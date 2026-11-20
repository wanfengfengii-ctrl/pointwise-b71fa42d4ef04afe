from dataclasses import dataclass

from receiving.rows import AcceptedRow


@dataclass(frozen=True, slots=True)
class Contribution:
    batch_id: str
    table_id: str
    sequence: int
    page_id: str
    line_no: int
    code: int
    segment: str
    milliseconds: int


def build_contributions(rows: tuple[AcceptedRow, ...]) -> tuple[Contribution, ...]:
    return tuple(
        Contribution(row.batch_id, row.table_id, row.sequence, row.page_id,
                     row.line_no, row.code, row.segment, row.milliseconds)
        for row in rows
    )
