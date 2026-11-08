"""Human-facing, deterministic display formatting shared across screens."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any


def format_game_date(value: Any, *, precision: str | None = None) -> str:
    """Format only provenance-confirmed timestamps; never infer time from legacy rows."""
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
    if precision != "timestamp":
        return "Время неизвестно"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return "Дата неизвестна"
        return parsed.astimezone().strftime("%d.%m.%Y · %H:%M")
    except (TypeError, ValueError, OverflowError):
        return "Дата неизвестна"


def format_game_activity_time(game: dict[str, Any]) -> str:
    """Show the same confirmed event timestamp used to order recent games."""
    last_move = game.get("last_move_at")
    last_move_ms = _valid_last_move_ms(last_move)
    if last_move_ms is not None:
        try:
            stamp = datetime.fromtimestamp(last_move_ms / 1000, tz=timezone.utc)
            label = "Последний ход" if game.get("status") in {
                "created", "started", "started_soon", "playing"} else "Завершена"
            return f"{label}: {stamp.astimezone().strftime('%d.%m.%Y · %H:%M')}"
        except (OverflowError, OSError, ValueError):
            pass
    precision = game.get("game_time_precision")
    value = format_game_date(game.get("started_at"), precision=precision)
    if value.startswith("Дата неизвестна"):
        return "Дата начала неизвестна"
    if value.startswith("Время неизвестно"):
        return "Время начала неизвестно"
    label = "Начата" if precision == "timestamp" and " · " in value else "Дата начала"
    return f"{label}: {value}"


def format_game_timeline(game: dict[str, Any]) -> str:
    """Show ordering event and, when known, the distinct start time."""
    activity = format_game_activity_time(game)
    if _valid_last_move_ms(game.get("last_move_at")) is None:
        return activity
    started = format_game_date(game.get("started_at"), precision=game.get("game_time_precision"))
    if started.startswith(("Дата неизвестна", "Время неизвестно")):
        return activity
    start_label = ("Начата" if game.get("game_time_precision") == "timestamp"
                   and " · " in started else "Дата начала")
    return f"{activity} · {start_label}: {started}"


def _valid_last_move_ms(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) and numeric > 0 else None
    except (OverflowError, ValueError):
        return None


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


def format_remaining_clock(milliseconds: int, *, elapsed_ms: int = 0,
                           active: bool = False, syncing: bool = False) -> tuple[str, bool]:
    """Format one authoritative clock snapshot plus a display-only monotonic tick."""
    if syncing:
        return "—:— · синхронизация", False
    remaining = max(0, int(milliseconds) - (max(0, int(elapsed_ms)) if active else 0))
    warning = remaining < 30_000
    if remaining <= 10_000:
        value = f"{remaining // 60_000}:{remaining // 1000 % 60:02}.{remaining % 1000 // 100}"
    elif remaining >= 3_600_000:
        hours, rest = divmod(remaining // 1000, 3600)
        minutes, seconds = divmod(rest, 60)
        if hours >= 24:
            days, hours = divmod(hours, 24)
            value = f"{days}д {hours:02}:{minutes:02}:{seconds:02}"
        else:
            value = f"{hours}:{minutes:02}:{seconds:02}"
    else:
        value = f"{remaining // 60_000:02}:{remaining // 1000 % 60:02}"
    return value, warning


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
    (("blackmar-diemer", "blackmar diemer"), "Гамбит Блэкмара — Димера"),
    (("old indian",), "Староиндийская защита"),
    (("indian defense", "indian defence"), "Индийская защита"),
    (("king's pawn game", "kings pawn game", "king's pawn opening", "kings pawn opening"),
     "Королевский пешечный дебют"),
    (("king's knight opening", "kings knight opening"), "Дебют королевского коня"),
    (("richter-veresov", "richter veresov"), "Атака Рихтера — Вересова"),
    (("rapport-jobava", "rapport jobava"), "Система Раппорта — Джобавы"),
    (("australian defense", "australian defence"), "Австралийская защита"),
    (("wade defense", "wade defence"), "Защита Уэйда"),
    (("czech defense", "czech defence"), "Чешская защита"),
    (("dresden opening",), "Дрезденский дебют"),
    (("irish gambit",), "Ирландский гамбит"),
    (("carr defense", "carr defence"), "Защита Карра"),
    (("goldsmith defense", "goldsmith defence"), "Защита Голдсмита"),
    (("duras gambit",), "Гамбит Дураса"),
    (("clemenz opening",), "Дебют Клеменца"),
    (("anderssen's opening", "anderssen opening"), "Дебют Андерсена"),
    (("amazon attack",), "Атака Амазонки"),
    (("saragossa opening",), "Сарагосский дебют"),
    (("marienbad system",), "Мариенбадская система"),
    (("vulture defense", "vulture defence"), "Защита Стервятник"),
    (("french defense", "french defence"), "Французская защита"),
    (("bogo-indian", "bogo indian"), "Защита Боголюбова"),
    (("nimzowitsch defense", "nimzowitsch defence", "nimzowitsch", "nimzovich"),
     "Защита Нимцовича"),
    (("nimzo-larsen", "nimzo larsen"), "Дебют Нимцовича — Ларсена"),
    (("philidor",), "Защита Филидора"),
    (("zukertort",), "Дебют Цукерторта"),
    (("van geet",), "Дебют Ван Геета"),
    (("van't kruijs", "van t kruijs"), "Дебют Ван Круйса"),
    (("bird opening", "bird's opening"), "Дебют Берда"),
    (("polish opening", "polish defense", "polish defence"), "Польское начало"),
    (("latvian gambit",), "Латвийский гамбит"),
    (("center game", "centre game"), "Дебют центра"),
    (("pterodactyl",), "Защита Птеродактиль"),
    (("grob",), "Дебют Гроба"),
    (("hungarian opening",), "Венгерское начало"),
    (("ponziani",), "Дебют Понциани"),
    (("reti", "réti"), "Дебют Рети"),
    (("englund",), "Гамбит Энглунда"),
    (("rat defense", "rat defence"), "Защита крысы"),
    (("benko gambit",), "Гамбит Бенко"),
    (("owen defense", "owen defence"), "Защита Оуэна"),
    (("kadas", "kádas"), "Дебют Кадаса"),
    (("ware opening", "ware defense", "ware defence"), "Дебют Уэра"),
    (("lion defense", "lion defence"), "Защита Льва"),
    (("three knights",), "Дебют трёх коней"),
    (("mieses",), "Дебют Мизеса"),
    (("sodium attack",), "Атака натрия"),
    (("mikenas",), "Защита Микенаса"),
    (("st. george", "saint george"), "Защита Святого Георгия"),
    (("barnes",), "Дебют Барнса"),
    (("amar opening",), "Дебют Амара"),
    (("portuguese",), "Португальская защита"),
    (("rubinstein",), "Система Рубинштейна"),
    (("blumenfeld",), "Контргамбит Блюменфельда"),
    (("horwitz",), "Защита Хорвицa"),
    (("kangaroo",), "Защита Кенгуру"),
    (("english defense", "english defence"), "Английская защита"),
    (("england gambit",), "Гамбит Энглунда"),
    (("danish gambit",), "Датский гамбит"),
    (("mexican defense", "mexican defence"), "Мексиканская защита"),
    (("robatsch",), "Защита Робача"),
    (("hippopotamus",), "Защита Бегемот"),
    (("bongcloud",), "Дебют Бонгклауд"),
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


def game_opening_name(game: dict[str, Any] | Any) -> str:
    """Return the canonical opening label for a persisted game row.

    Lichess imports store the ECO in ``opening_eco`` while older/local rows may
    use ``eco``. Prefer the imported canonical value so every screen presents
    the same opening for the same game.
    """
    if not hasattr(game, "get"):
        return russian_opening_name(None, None)
    return russian_opening_name(game.get("opening_eco") or game.get("eco"),
                                game.get("opening_name"))


def opening_tooltip(name: Any) -> str:
    """Keep the catalog's exact English name available as secondary detail."""
    value = str(name or "").strip()
    return f"Название в исходном каталоге: {value}" if value else "Название дебюта отсутствует в PGN."


def source_short(source: Any) -> tuple[str, str]:
    raw = str(source or "").strip().casefold()
    if raw in {"stockfish", "local", "chess_coach"} or "stockfish" in raw:
        return "S", "Stockfish · локальная оценка"
    if raw in {"lichess", "lichess_analysis", "official_lichess", "lichess-pgn",
               "lichess pgn", "lichess-json", "lichess json"}:
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
        if int(mate_white) == 0:
            return "Мат в текущей позиции · перспектива белых"
        side = "белые ставят мат" if mate_white > 0 else "чёрные ставят мат"
        return f"{side} через {abs(int(mate_white))} · перспектива белых"
    if white_cp is None:
        return "оценка отсутствует"
    return f"{int(white_cp) / 100:+.2f} за белых"
