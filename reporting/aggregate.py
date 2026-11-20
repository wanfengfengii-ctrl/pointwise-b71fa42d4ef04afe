from reporting.contributions import Contribution


def aggregate_segments(contributions: tuple[Contribution, ...]) -> tuple[tuple[str, int], ...]:
    totals: dict[str, int] = {}
    for contribution in contributions:
        segment = contribution.segment
        totals[segment] = totals.get(segment, 0) + contribution.milliseconds
    return tuple(totals.items())
