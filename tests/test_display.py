from chess_coach.ui.display import (
    format_clock_control, format_game_date, russian_opening_name, source_short,
)


def test_local_timestamp_date_precision_and_invalid_date_are_honest():
    assert format_game_date("2026-11-02") == "02.11.2026"
    assert format_game_date("2026-11-02T12:05:39.123Z").startswith("02.11.2026 · ")
    assert "+00:00" not in format_game_date("2026-11-02T12:05:39Z")
    assert format_game_date("2026-11-02T12:05:39Z", precision="date_only") == "02.11.2026"
    assert format_game_date("invalid") == "Дата неизвестна"
    assert format_game_date(20261102) == "02.11.2026"


def test_clock_labels_preserve_units_for_base_and_increment():
    assert format_clock_control("300+3") == "5 мин + 3 с"
    assert format_clock_control("10800+180") == "3 ч + 3 мин"
    assert format_clock_control("0+1") == "0 с + 1 с"
    assert format_clock_control("daily:86400") == "1 день на ход"
    assert format_clock_control("unknown") == "unknown"


def test_opening_families_translate_deterministically_and_keep_eco():
    assert russian_opening_name("B20", "Sicilian Defense") == "B20 · Сицилианская защита"
    assert russian_opening_name("C50", "Italian Game") == "C50 · Итальянская партия"
    assert russian_opening_name("D05", "") == "D05 · Лондонская система"
    assert russian_opening_name("Z99", "Rare English line") == "Z99 · дебют"
    assert russian_opening_name("", "French Defense: Exchange") == "Французская защита"


def test_source_badges_have_accessible_explanations():
    assert source_short("stockfish")[0] == "S"
    assert "Lichess" in source_short("lichess")[1]
    assert source_short(None) == ("—", "Оценка отсутствует")
