from datetime import datetime

import pytest

from app.timeparse.ru import creation_moment, parse
from tests.conftest import NOW  # вторник, 20.10.2026 19:59


@pytest.mark.parametrize("text, expected, has_time", [
    ("завтра в 10 утра", datetime(2026, 10, 21, 10, 0), True),
    ("в пятницу", datetime(2026, 10, 23), False),
    ("в четверг в 11", datetime(2026, 10, 22, 11, 0), True),
    ("послезавтра", datetime(2026, 10, 22), False),
    ("через два часа", datetime(2026, 10, 20, 21, 59), True),
    ("через полчаса", datetime(2026, 10, 20, 20, 29), True),
    ("через неделю в 10", datetime(2026, 10, 27, 10, 0), True),
    ("послезавтра в 3 дня", datetime(2026, 10, 22, 15, 0), True),
    ("20 ноября в 9:30", datetime(2026, 11, 20, 9, 30), True),
    ("двадцать пятого октября", datetime(2026, 10, 25), False),
    ("21.10", datetime(2026, 10, 21), False),
    ("в 10.30", datetime(2026, 10, 21, 10, 30), True),          # время сегодня прошло → завтра
    ("в 7 вечера", datetime(2026, 10, 21, 19, 0), True),
    ("в 3", datetime(2026, 10, 21, 15, 0), True),                # «в 3» в деловом контексте — 15:00
    ("в 9", datetime(2026, 10, 21, 9, 0), True),
    ("в час дня", datetime(2026, 10, 21, 13, 0), True),
    ("в половине третьего", datetime(2026, 10, 21, 14, 30), True),
    ("в следующую среду в полдень", datetime(2026, 10, 28, 12, 0), True),
    ("в пятницу на следующей неделе", datetime(2026, 10, 30), False),
    ("сегодня вечером", datetime(2026, 10, 20, 19, 0), True),
    ("2026-10-23T10:00", datetime(2026, 10, 23, 10, 0), True),
    ("завтра в восемь тридцать утра", datetime(2026, 10, 21, 8, 30), True),   # время словами, как диктуют
    ("в пятницу в четырнадцать ноль ноль", datetime(2026, 10, 23, 14, 0), True),
    ("в девять ноль пять", datetime(2026, 10, 21, 9, 5), True),
    ("в двадцать три сорок пять", datetime(2026, 10, 20, 23, 45), True),
    ("в пять двадцать пятого октября", datetime(2026, 10, 25, 17, 0), True),  # «двадцать пятого» — дата
])
def test_moments(text, expected, has_time):
    info = parse(text, NOW)
    assert info.moment == expected
    assert info.has_time == has_time


@pytest.mark.parametrize("text, start, end", [
    ("на этой неделе", datetime(2026, 10, 19), datetime(2026, 10, 26)),
    ("на следующей неделе", datetime(2026, 10, 26), datetime(2026, 11, 2)),
    ("на прошлой неделе", datetime(2026, 10, 12), datetime(2026, 10, 19)),
    ("в выходные", datetime(2026, 10, 24), datetime(2026, 10, 26)),
    ("в октябре", datetime(2026, 10, 1), datetime(2026, 11, 1)),
    ("в следующем месяце", datetime(2026, 11, 1), datetime(2026, 12, 1)),
    ("сегодня", datetime(2026, 10, 20), datetime(2026, 10, 21)),
])
def test_query_ranges(text, start, end):
    assert parse(text, NOW, "nearest").query_range() == (start, end)


def test_deadline_end_of_week():
    info = parse("купить билеты до конца недели", NOW)
    assert info.deadline
    assert info.moment == datetime(2026, 10, 25)
    assert info.remaining_text() == "купить билеты"


def test_past_preference():
    assert parse("в пятницу", NOW, "past").moment == datetime(2026, 10, 16)
    assert parse("в прошлую пятницу", NOW).moment == datetime(2026, 10, 16)


def test_duration_is_not_time():
    info = parse("встреча завтра в 15:00 на 2 часа", NOW)
    assert info.moment == datetime(2026, 10, 21, 15, 0)
    assert info.duration_min == 120


def test_numbers_in_content_are_not_time():
    info = parse("купить 2 билета в кино", NOW)
    assert not info.found
    assert info.remaining_text() == "купить 2 билета в кино"


def test_creation_moment_for_period():
    due, has_time = creation_moment(parse("на следующей неделе", NOW), NOW)
    assert due == datetime(2026, 10, 26) and not has_time
