from tables.catalog import CodeTable


def resolve_code(table: CodeTable, code: int) -> str:
    if type(code) is not int:
        raise ValueError('原行代码须为整数')
    try:
        return table.by_code[code]
    except KeyError as error:
        raise ValueError(f'代码未在表 {table.table_id!r} 中登记') from error
