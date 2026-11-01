# M4.5 — синтетические примеры отчётов

Все примеры ниже составлены только из фикстур тестов. Они не описывают партии владельца и не являются игровыми рекомендациями.

## Паспорт одной партии

Фикстура `metric-game` из `tests/test_m45_move_metrics.py`:

- Источник оценки: `lichess-pgn`; локальный Stockfish не запускался.
- Официальная Lichess accuracy белых: 74.5% — отдельная метрика партии, источник явно указан.
- Оценки: ply 0 = 0 CP, ply 1 = +100 CP, ply 2 = +20 CP.
- Сохранённые оценки покрывают обе позиции до/после ходов. Локальная точность по собственной формуле не заявлена.
- `latest_analysis_detail` лениво строит versioned move metrics из сохранённых фактов; движка для открытия паспорта нет.

## Пример кандидата на упущенную возможность

Фикстура `synthetic-opportunity` из `tests/test_m45_move_metrics.py`:

- До ошибки соперника сохранён лучший PV `e2e4 e7e5`; после ответного хода сравнимые оценки меняются с −120 до +135 CP с точки зрения белых.
- Система создаёт `missed_opportunity`, `confidence_state=candidate`, указывает ply создания возможности и позицию, откуда взят best move.
- Это **не подтверждённое использование/пропуск**: одиночный PV не доказывает тактический мотив и не даёт denominator для процента «использованных возможностей».

## Неопределённый тактический мотив

Каталог из `tests/test_m45_event_detectors.py` регистрирует fork/pin/skewer как `deferred`. Они не выводятся из CP-loss и не выдаются как обнаруженная тактика. При отсутствии отдельного доказательства статус остаётся unknown/недоступен.

## Текущий профиль и сравнение периодов

Фикстура `tests/test_m45_profiles.py` создаёт 30 синтетических полностью покрытых белых партий в последние 90 дней, одну чёрную и одну недатированную. Профиль показывает отдельные цвета, среднюю official accuracy с `n/N`, а недатированную игру сохраняет в истории без попадания в недавнее окно. Ещё одна фикстура показывает три партии с opening mistakes, связанные с `game_id`/`ply`; так как имеется только три оценённых собственных дебютных хода, гипотеза о проблеме дебюта помечается как недостаточно обоснованная.

Сравнение периодов остаётся предварительным, если в одном из окон меньше 30 партий или analysis coverage различается более чем на 10 процентных пунктов.

## Сводка ошибок

Синтетическая фикстура `metric-game` подтверждает раздельность источников: официальные 74.5% остаются Lichess game accuracy; локальная точность остаётся `null`; CP-loss и severity имеют source/version provenance. Нельзя усреднять официальный и локальный показатели.

## Набор из 20 ожидаемых мини-отчётов

Ниже перечислены 20 hand-authored synthetic cases, автоматически проверяемых параметризованным тестом `tests/test_m45_synthetic_reports.py`. Centipawn loss считается с POV игрока, который ходил. Во всех локальных synthetic reports локальная accuracy остаётся unavailable (`null`); значения accuracy в строках с `lichess-pgn` — отдельная официальная game accuracy, не смешиваемая с локальной оценкой. `coverage` показывает полноту оценок для примера, `candidate` — неподтверждённое событие.

| ID | Source | Цвет | CP loss | Severity | Coverage | Official accuracy | Events |
| --- | --- | --- | ---: | --- | --- | ---: | --- |
| white-loss-0 | Stockfish | белые | 0 | good | complete | — | — |
| white-loss-49 | Stockfish | белые | 49 | good | complete | — | — |
| white-loss-50 | Stockfish | белые | 50 | inaccuracy | complete | — | — |
| white-loss-99 | Stockfish | белые | 99 | inaccuracy | complete | — | — |
| white-loss-100 | Lichess PGN | белые | 100 | mistake | complete | 74.5% | — |
| white-loss-199 | Stockfish | белые | 199 | mistake | complete | — | — |
| white-loss-200 | Stockfish | белые | 200 | blunder | complete | — | — |
| white-loss-310 | Stockfish | белые | 310 | blunder | complete | — | — |
| black-loss-0 | Stockfish | чёрные | 0 | good | complete | — | — |
| black-loss-49 | Stockfish | чёрные | 49 | good | complete | — | — |
| black-loss-50 | Stockfish | чёрные | 50 | inaccuracy | complete | — | — |
| black-loss-99 | Stockfish | чёрные | 99 | inaccuracy | complete | — | — |
| black-loss-100 | Lichess PGN | чёрные | 100 | mistake | complete | 69.0% | — |
| black-loss-199 | Stockfish | чёрные | 199 | mistake | complete | — | — |
| black-loss-200 | Stockfish | чёрные | 200 | blunder | complete | — | — |
| black-loss-310 | Stockfish | чёрные | 310 | blunder | complete | — | — |
| mate-transition | Stockfish | белые | unknown | unknown | partial | — | — |
| missing-score | Lichess PGN | белые | unknown | unknown | partial | 55.0% | — |
| missing-position | Lichess PGN | белые | no fact | unknown | partial | — | — |
| candidate-opportunity | Stockfish | обе стороны | 120 / 125 | mistake / mistake | complete | — | candidate only |

Этот набор проверяет пороги 50/100/200 CP с обоих POV, 0 CP, mate discontinuity, неполный score/position, provenance и запрет считать opportunity подтверждённой. Он является компактным regression/sample-report corpus, а не самостоятельной оценкой силы классификатора на реальных партиях.

## Незакрытое требование примеров

Полностью **verified** opportunity conversion сейчас недоступен: код создаёт только candidate missed-opportunity, а устойчивой связи «возможность → последующий выбранный ход» нет. Поэтому конверсия всегда `null` с причиной отсутствия подтверждённой связи; процент из чисел несвязанных событий намеренно не выводится.
