from collections.abc import Iterable, Mapping


def require_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}须为非空文本')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'{label}须能以 UTF-8 表示') from error
    return value


def normalize_entries(
    entries: Mapping[int, str] | Iterable[tuple[int, str]],
) -> tuple[tuple[int, str], ...]:
    pairs = entries.items() if isinstance(entries, Mapping) else entries
    try:
        iterator = iter(pairs)
    except TypeError as error:
        raise ValueError('码表须由代码与工段条目组成') from error

    normalized: dict[int, str] = {}
    for pair in iterator:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError('码表条目须含代码和工段')
        code, segment = pair
        if type(code) is not int:
            raise ValueError('码表代码须为整数')
        name = require_text(segment, '工段名')
        if code in normalized and normalized[code] != name:
            raise ValueError('同一表内同一代码的工段冲突')
        normalized[code] = name
    if not normalized:
        raise ValueError('码表须含至少一个代码')
    return tuple(sorted(normalized.items()))
