"""Human-facing, deterministic display formatting shared across screens."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


def format_game_date(value: Any, *, precision: str | None = None) -> str:
    """Format exact timestamps in local time; preserve date-only precision."""
    if value is None or value == "":
        return "Дата неизвестна"
    if precision == "date_only":
        raw = str(value)
        try:
            return datetime.fromisoformat(raw[:10]).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return "Дата неизвестна"
    if isinstance(value, (int, float)):
        # Numeric archive dates are treated as date precision unless metadata
        # explicitly says otherwise; they are commonly YYYYMMDD, not epoch.
        raw = str(int(value))
        if re.fullmatch(r"\d{8}", raw):
            try:
                return datetime.strptime(raw, "%Y%m%d").strftime("%d.%m.%Y")
            except ValueError:
                return "Дата неизвестна"
        return "Дата неизвестна"
    raw = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return datetime.fromisoformat(raw).strftime("%d.%m.%Y")
        except ValueError:
            return "Дата неизвестна"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return "Дата неизвестна"
        return parsed.astimezone().strftime("%d.%m.%Y · %H:%M")
    except (TypeError, ValueError, OverflowError):
        return "Дата неизвестна"


def _seconds(value: int) -> str:
    if value >= 86400 and value % 86400 == 0:
        days = value // 86400
        return f"{days} дн." if days > 1 else "1 день"
    if value >= 3600 and value % 60 == 0:
        hours, rem = divmod(value, 3600)
        return f"{hours} ч" + (f" + {rem // 60} мин" if rem else "")
    if value >= 60 and value % 60 == 0:
        return f"{value // 60} мин"
    return f"{value} с"


def format_clock_control(value: Any) -> str:
    if value is None or value == "" or value == "-":
        return "Контроль не указан"
    raw = str(value).strip()
    lowered = raw.casefold()
    daily = re.fullmatch(r"(?:daily:)?(\d{4,6})", lowered)
    if "day" in lowered or "дн" in lowered or daily:
        seconds = int(daily.group(1)) if daily else 86400
        return f"{_seconds(seconds)} на ход"
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)", raw)
    if match:
        base, increment = map(int, match.groups())
        return f"{_seconds(base)} + {_seconds(increment)}"
    return raw


_OPENING_FAMILIES = (
    (("queen's pawn", "queens pawn"), "Дебют ферзевой пешки"),
    (("elephant gambit",), "Слоновий гамбит"),
    (("king's indian", "kings indian"), "Староиндийская защита"),
    (("nimzo-indian", "nimzo indian"), "Защита Нимцовича"),
    (("queen's indian", "queens indian"), "Новоиндийская защита"),
    (("catalan",), "Каталонское начало"),
    (("benoni",), "Защита Бенони"),
    (("semi-slav", "semi slav"), "Полуславянская защита"),
    (("slav",), "Славянская защита"),
    (("king's gambit", "kings gambit"), "Королевский гамбит"),
    (("vienna",), "Венская партия"),
    (("scotch",), "Шотландская партия"),
    (("four knights",), "Дебют четырёх коней"),
    (("pirc",), "Защита Пирца"),
    (("modern defense", "modern defence"), "Современная защита"),
    (("scandinavian",), "Скандинавская защита"),
    (("trompowsky",), "Атака Тромповского"),
    (("colle",), "Система Колле"),
    (("torre attack",), "Атака Торре"),
    (("london system", "london defense", "london defence", "london opening"), "Лондонская система"),
    (("sicilian", "sicilian defense", "sicilian defence"), "Сицилианская защита"),
    (("caro-kann", "caro kann"), "Защита Каро-Канн"),
    (("french",), "Французская защита"),
    (("alekhine", "alekhine's"), "Защита Алехина"),
    (("italian", "giuoco piano"), "Итальянская партия"),
    (("queen's gambit", "queens gambit"), "Ферзевый гамбит"),
    (("english opening", "english game"), "Английское начало"),
    (("grunfeld", "grünfeld"), "Защита Грюнфельда"),
    (("russian", "petroff", "petrov"), "Русская партия"),
    (("bishop's opening", "bishops opening"), "Дебют слона"),
    (("ruy lopez", "spanish"), "Испанская партия"),
    (("dutch",), "Голландская защита"),
)


def russian_opening_name(eco: Any, name: Any) -> str:
    """Translate known opening families locally; retain ECO and English detail."""
    code = str(eco or "").strip().upper()
    raw = str(name or "").strip()
    folded = raw.casefold()
    for tokens, label in _OPENING_FAMILIES:
        if any(token in folded for token in tokens):
            return f"{code} · {label}" if code else label
    # ECO family fallback is deliberately limited to well-known ranges.
    first = code[:1]
    try:
        number = int(code[1:])
    except ValueError:
        number = -1
    label = None
    if first == "B" and 20 <= number <= 99: label = "Сицилианская защита"
    elif first == "B" and 10 <= number <= 19: label = "Защита Каро-Канн"
    elif first == "B" and 2 <= number <= 5: label = "Защита Алехина"
    elif first == "C" and 0 <= number <= 19: label = "Французская защита"
    elif first == "C" and 50 <= number <= 59: label = "Итальянская партия"
    elif first == "C" and 60 <= number <= 99: label = "Испанская партия"
    elif first == "D" and 6 <= number <= 69: label = "Ферзевый гамбит"
    elif first == "D" and 80 <= number <= 99: label = "Защита Грюнфельда"
    elif first == "A" and 10 <= number <= 39: label = "Английское начало"
    elif first == "A" and 80 <= number <= 99: label = "Голландская защита"
    elif code in {"C42", "C43"}: label = "Русская партия"
    elif code in {"C23", "C24"}: label = "Дебют слона"
    if label:
        return f"{code} · {label}"
    if code:
        return f"{code} · дебют не классифицирован"
    return "Дебют не определён" if not raw else "Дебют не классифицирован"


def opening_tooltip(name: Any) -> str:
    """Keep the catalog's exact English name available as secondary detail."""
    value = str(name or "").strip()
    return f"Название в исходном каталоге: {value}" if value else "Название дебюта отсутствует в PGN."


def source_short(source: Any) -> tuple[str, str]:
    raw = str(source or "").strip().casefold()
    if raw in {"stockfish", "local", "chess_coach"} or "stockfish" in raw:
        return "S", "Stockfish · локальная оценка"
    if raw in {"lichess", "lichess_analysis", "official_lichess", "lichess-pgn",
               "lichess pgn"}:
        return "L", "Lichess · официальный импортированный анализ"
    return "—", "Оценка отсутствует"


def severity_label(value: Any) -> str:
    return {
        "good": "хороший ход", "inaccuracy": "неточность", "mistake": "ошибка",
        "blunder": "зевок", "unknown": "не оценён", "strong": "подтверждённый сильный ход",
    }.get(str(value or "").casefold(), "класс не определён")


def phase_label(value: Any) -> str:
    return {"opening": "дебют", "middlegame": "миттельшпиль",
            "endgame": "эндшпиль", "unknown": "этап не определён"}.get(
                str(value or "").casefold(), "этап не определён")


def evaluation_label(white_cp: int | None, mate_white: int | None = None) -> str:
    if mate_white is not None:
        side = "белые ставят мат" if mate_white > 0 else "чёрные ставят мат"
        return f"{side} через {abs(int(mate_white))} · перспектива белых"
    if white_cp is None:
        return "оценка отсутствует"
    return f"{int(white_cp) / 100:+.2f} за белых"
