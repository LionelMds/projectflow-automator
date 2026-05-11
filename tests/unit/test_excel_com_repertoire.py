from __future__ import annotations

from datetime import date, datetime, time

from projectflow.core.excel_com_repertoire import _range_values_to_rows, _to_excel_value


def test_excel_com_range_values_to_rows_handles_shapes() -> None:
    assert _range_values_to_rows("A", rows=1, columns=1) == [["A"]]
    assert _range_values_to_rows(("A", "B"), rows=1, columns=2) == [["A", "B"]]
    assert _range_values_to_rows((("A",), ("B",)), rows=2, columns=1) == [["A"], ["B"]]
    assert _range_values_to_rows(
        (("A", "B"), ("C", "D")),
        rows=2,
        columns=2,
    ) == [["A", "B"], ["C", "D"]]


def test_excel_com_date_values_are_written_as_datetimes() -> None:
    value = _to_excel_value(date(2026, 5, 11))

    assert value == datetime.combine(date(2026, 5, 11), time.min)
