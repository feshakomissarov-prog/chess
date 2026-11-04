import csv
from pathlib import Path

from chess_coach.ui.display import (
    evaluation_label, format_clock_control, format_game_date, format_remaining_clock,
    russian_opening_name,
    source_short,
)


def test_local_timestamp_date_precision_and_invalid_date_are_honest():
    assert format_game_date("2026-11-02") == "02.11.2026"
    assert format_game_date("2026-11-02T12:05:39.123Z").startswith("Время неизвестно")
    assert format_game_date("2026-11-02T12:05:39Z", precision="timestamp").startswith("02.11.2026 · ")
    assert "+00:00" not in format_game_date("2026-11-02T12:05:39Z", precision="timestamp")
    assert format_game_date("2026-11-02T12:05:39Z", precision="date_only") == "02.11.2026"
    assert format_game_date("2026-08-04T00:00:00+00:00", precision="date_only") == "04.08.2026"
    assert format_game_date("invalid") == "Время неизвестно"
    assert format_game_date(20261102) == "02.11.2026"


def test_clock_labels_preserve_units_for_base_and_increment():
    assert format_clock_control("300+3") == "5 мин + 3 с"
    assert format_clock_control("10800+180") == "3 ч + 3 мин"
    assert format_clock_control("0+1") == "0 с + 1 с"
    assert format_clock_control("daily:86400") == "1 день на ход"
    assert format_clock_control("unknown") == "unknown"


def test_remaining_clock_formatting_ticks_only_active_snapshot_and_marks_low_time():
    assert format_remaining_clock(180_000) == ("03:00", False)
    assert format_remaining_clock(180_000, elapsed_ms=1_200, active=True) == ("02:58", False)
    assert format_remaining_clock(29_000) == ("00:29", True)
    assert format_remaining_clock(9_900, active=True, elapsed_ms=100) == ("0:09.8", True)
    assert format_remaining_clock(3_661_000) == ("1:01:01", False)
    assert format_remaining_clock(24 * 60 * 60 * 1000 + 3_661_000) == (
        "1д 01:01:01", False)
    assert format_remaining_clock(90_000, elapsed_ms=9_000, active=True, syncing=True) == (
        "—:— · синхронизация", False)


def test_opening_families_translate_deterministically_and_keep_eco():
    assert russian_opening_name("B20", "Sicilian Defense") == "B20 · Сицилианская защита"
    assert russian_opening_name("C50", "Italian Game") == "C50 · Итальянская партия"
    assert russian_opening_name("A40", "Queen's Pawn Game") == "A40 · Дебют ферзевой пешки"
    assert russian_opening_name("B00", "Elephant Gambit") == "B00 · Слоновий гамбит"
    assert russian_opening_name("D01", "Queen's Pawn Game: Chigorin Variation") == (
        "D01 · Дебют ферзевой пешки")
    assert russian_opening_name("D05", "Queen's Pawn Game: Colle System") == (
        "D05 · Дебют ферзевой пешки")
    assert russian_opening_name("D05", "") == "D05 · дебют не классифицирован"
    assert russian_opening_name("D01", "Unknown Indian line") == (
        "D01 · дебют не классифицирован")
    assert russian_opening_name("D03", "Unknown Queen Pawn variation") == (
        "D03 · дебют не классифицирован")
    assert russian_opening_name("C40", "Philidor Defense: Hanham Variation") == (
        "C40 · Защита Филидора")
    assert russian_opening_name("A00", "Grob Opening: Grob Gambit") == (
        "A00 · Дебют Гроба")
    assert russian_opening_name("Z99", "Rare English line") == "Z99 · дебют не классифицирован"
    assert russian_opening_name("", "French Defense: Exchange") == "Французская защита"


def test_source_badges_have_accessible_explanations():
    assert source_short("stockfish")[0] == "S"
    assert "Lichess" in source_short("lichess")[1]
    assert source_short("lichess-json")[0] == "L"
    assert source_short("lichess-pgn")[0] == "L"
    assert source_short(None) == ("—", "Оценка отсутствует")


def test_terminal_mate_label_never_reports_mate_in_zero():
    assert evaluation_label(None, 3).startswith("белые ставят мат через 3")
    assert evaluation_label(None, -2).startswith("чёрные ставят мат через 2")
    assert evaluation_label(None, 0) == "Мат в текущей позиции · перспектива белых"


def test_opening_catalog_translation_coverage_is_reported_and_high_without_guessing():
    root = Path(__file__).parents[1] / "src" / "chess_coach" / "opening_data"
    pairs = set()
    for path in root.glob("*.tsv"):
        with path.open(encoding="utf-8", newline="") as source:
            pairs.update((row["eco"], row["name"])
                         for row in csv.DictReader(source, delimiter="\t"))
    translated = [(eco, name) for eco, name in pairs
                  if "не классифицирован" not in russian_opening_name(eco, name)]
    assert len(pairs) == 3332
    assert len(translated) / len(pairs) >= 0.99
    assert russian_opening_name("D03", "") == "D03 · дебют не классифицирован"
