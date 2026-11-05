# Chess Coach — Lichess Accuracy и форсированный мат Stockfish: исследование и технический контракт V1

**Дата исследования: 05.11.2026. Статус: нормативное дополнение к REVIEW-FIX-06, пункты RF6-09—RF6-11.** Сохранённый на GitHub источник знаний; нельзя подменять опубликованный на Lichess процент локальным расчётом. Оценка и анализ UCI во время живой онлайн-партии с человеком строго запрещены (fail-closed Fair Play).

## 1. Первичные источники Lichess и Stockfish

- **Официальное описание:** https://lichess.org/page/accuracy — Win%, move accuracy, volatility-weighted mean + harmonic mean.
- **Текущий код Lichess:** https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/AccuracyPercent.scala . Проверенный blob SHA a46b4e6d9d80a100c2a617db8bbc558bcec918d5. Он содержит точные коэффициенты, бонус +1, алгоритм окон. Это новее округлённого примера на lichess.org.
- **Scalachess оценки и мат:** https://github.com/lichess-org/scalachess/blob/master/core/src/main/scala/eval.scala . Проверенный blob SHA e318e5ed9218140b37c344796592b18955d0e04d. WinPercent.fromScore, fromMate, CP.CEILING=1000, MateGiven и Mate(0).
- **Lichess tree score:** https://github.com/lichess-org/lila/blob/master/modules/tree/src/main/eval.scala . Проверенный blob SHA c1b8ddc029bb6361a631200b597a0cdd72c211ab. forceAsCp — только внутреннее преобразование mate для gameAccuracy. Исправление именно mate=0 было внесено в Lichess в сентябре 2026: https://github.com/lichess-org/lila/issues/21104 .
- **Условия ошибочного/упущенного мата:** https://github.com/lichess-org/lila/blob/master/modules/tree/src/main/Advice.scala . MateCreated/MateLost/MateDelayed оцениваются отдельно от CP. Не выдавать каждую смену расстояния до мата за ошибку.
- **Контракт python-chess:** https://python-chess.readthedocs.io/en/stable/engine.html . Score.score(mate_score=None) возвращает None для Mate; Score.mate() даёт матовый тип. score(mate_score=100000) выдаёт ИСКУССТВЕННЫЙ CP surrogate, а не позиционную оценку. Python-chess WDL с model="sf" отличается от WinPercent Lichess.

Фиксировать новые SHA и bump formula_id при изменении upstream. Не считать текст официальной страницы, содержащий округлённые коэффициенты и не показывающий бонус +1, точнее текущей реализации Scala. Для воспроизводимости тестов сохранить наши golden-значения и reference SHA в коде/документах.

## 2. Формула и расчёт

**CP → процент вероятности победы белых:**

    c = max(-1000, min(1000, white_cp))
    win_white = 50 + 50 * (2 / (1 + exp(-0.00368208*c)) - 1)

Golden: CP=0 → 50%; CP=+1000 → 97.5447436341%; CP=-1000 → 2.4552563659%.

**Mate без фиктивных CP:** white mate > 0 → win_white от виртуального +1000; white mate < 0 → от виртуального -1000. White mate=0 означает, что сторона, которой сейчас ходить, уже заматована. Победитель определяется легальной текущей доской и её turn; победили белые, если заматованы чёрные. Для MateGiven — победа стороны, только что поставившей мат. Это внутренний surrogate исключительно для Win%/графика вероятности; в хранилище white_cp остаётся NULL, а UI показывает M3/M-3/Мат. При Mate(0) на нетерминальной позиции без легально подтверждённого checkmate нельзя выдумывать исход. Не нужно переводить M3 в +100 или +999 пешек.

**Точность отдельного хода:** ориентироваться на POV сделавшего ход, не всегда на белых: для чёрных moverWin=100-win_white. Если winAfter>=winBefore: accuracy=100. Иначе:

    loss = winBefore - winAfter
    accuracy = clamp(
        103.1668100711649*exp(-0.04354415386753951*loss)
        - 3.166924740191411 + 1.0,
        0, 100
    )

Бонус +1 — uncertainty bonus в актуальном Lichess source. Равный выигрышный мат M5→M3 по этому методу имеет accuracy 100, потому что не изменилась победившая сторона; упущенный собственный M3→мат соперника даёт резкую потерю Win%. Длина forced mate влияет на текст, не должна сама превращаться в ошибку этой модели.

**Точность всей партии:** из [initial, after ply1, ..., after last ply] построить список Win% и movewise accuracy. Window size=clamp(floor(number_of_plies/10), 2, 8), окна по актуальному AccuracyPercent.scala с начальными повторными окнами; для каждого окна вес=clamp(stddev(winPercent), 0.5, 12). Для белых/чёрных раздельно вычислить weighted mean точностей их ходов и harmonic mean; game accuracy=(weightedMean+harmonicMean)/2. Должно совпадать с reference Scala на golden fixtures, в том числе короткие партии, black-to-move custom FEN, 0 accuracy и весь mate line. Не подменять расчёт арифметическим средним или CPL.

**Различие моделей:** Lichess WinPercent (эмпирическая sigmoid) ≠ современный Stockfish WDL/PovWdl (sf, sf16, etc.). Не пытаться считать Win% через уже сохранённый столбец move_metrics.wdl_before_json. Lichess-style Accuracy ≠ официальная accuracy из Lichess export, так как глубина, версия Stockfish, источник initial eval и количество сохранённых оценок могут отличаться.

## 3. Текущее состояние проекта PR #44 (SHA 881a8d8f...) — не переписывать с нуля

**Уже работает:**
- src/chess_coach/domain/accuracy.py: win_percent_cp, move_accuracy, _game_scores, _accuracy_by_side, calculate_saved_game_accuracy. Уже реализованы CP cap, +1, weighted/harmonic и базовая обработка mate(0); FORMULA_ID сейчас lichess-accuracy-2e653ad1-v1.
- src/chess_coach/lichess/game_review.py::load_game_review **уже вызывает** calculate_saved_game_accuracy; ui/game_review.py уже различает официальный процент Lichess и локальный расчёт Chess Coach. Не заявлять, что расчёта всей партии совсем нет.
- src/chess_coach/lichess/analysis.py: полный Stockfish и selective review правильно хранят white_cp=score.score(mate_score=None) отдельно от mate_white=score.mate().

**Выявленные пробелы и конкретный дефект:**
- src/chess_coach/lichess/move_metrics.py::build_move_facts: accuracy=None, accuracy_source=None, accuracy_formula_id=None даже при двух сравнимых CP/Mate позициях. Когда присутствует mate, cp_loss=None и severity="unknown"; поэтому не считается точность отдельного хода, в том числе форсированный мат; прогресс видит только CPL.
- src/chess_coach/position_analysis.py::evaluate_position вызывает Score.score(mate_score=100000) и кладёт искусственное «почти 100000 CP» в score_white_cp рядом с mate_white. Исправить типизацию в этом API и во всех потребителях: CP должен быть None при mate, mate передаётся отдельно.
- ui/game_review.py::select_ply явно передаёт score_for_bar=None для всех матовых позиций: вертикальная шкала оценки пустеет в момент мата, хотя точный знак преимущества известен. В label M# сохраняется; полоска может использовать Win% по матовому типу с правильной ориентацией, но без подмены обычного CP.
- move details/ReviewGraph показывают Δ: «н/д из-за мата» и могут оставлять качество UNKNOWN даже для доказанного упущения/появления forced mate. Нужен отдельный mate-event с доказательством before/after и per-move Accuracy при достаточной паре оценок.
- tests/test_accuracy.py уже содержит базовые 7 кейсов, но нет достаточного goldens для полной интеграции со Scala/terminal/матовыми состояниями/показом UI.

## 4. Исполняемые задачи для RF6-09—RF6-11

### RF6-09 — точный версионированный Lichess-method engine

1. Сверить существующий domain/accuracy.py с актуальной Scala на golden fixtures и детерминированной паре одинаково сохранённых позиционных оценок; исправить лишь выявленные численные отклонения: окна, stdev, нулевая harmonic mean, чередование white/black, старт FEN с чёрным к ходу, mate=0, first/last terminal. Версионировать formula_id и source blob hash; историю не затирать.
2. Calculated local game accuracy только для complete current line с достаточным корректным score data, n/N и параметрами глубины, не по classified-only, не по устаревшей revision/hash. Там, где engine не оценил нормальную нетерминальную позицию, нет процентов; это не Win%=50.
3. Проверенный terminal checkmate со score отсутствует: можно получить terminal score только из LEGAL board.is_checkmate(), с отдельной provenance board-terminal (не считать это «Stockfish depth 20»). При nonterminal missing score, draw, stalemate, repetition и противоречивом Mate(0) отсутствие или ошибка данных остаются неизвестностью, без домыслов.
4. Официальную точность Lichess с сервера не пересчитывать и не перезаписывать; локальную показывать как «Chess Coach, метод Lichess, неофициально», отдельные поля origin/version/depth/coverage. Не менять существующие готовые партии, только чтение/добавочное versioned materialization; не запускать UCI при чтении UI.

### RF6-10 — матовые типы Stockfish, точность хода, события качества

1. Один canonical typed score adapter для CP/Mate/MateGiven/Unknown и POV позиции. Переходить от src/chess_coach/position_analysis.py synthetic mate_score=100000 к score.score(mate_score=None) + mate_white; проверить все UI call sites и Bar/Graph/quick eval на отсутствие подмены матов CP числами.
2. Для каждой оценённой пары adjacent positions (до/после move) вычислять mover move_accuracy по WinPercent для CP/Mate. Новое метаполе accuracy_source=stockfish-local-lichess-method и accuracy_formula_id; при пробеле в паре accuracy=None, не догадка. Сохранить cp_loss=None в mate-pair, поскольку численного сравнимого CPL нет.
3. Отдельно детектировать evidence-driven mate-events: сохранение форсированного мата своей стороны; упущение своего форсированного мата; позволили форсированный мат соперника; смена знака мата; реализованный мат=0. Метаинформация: before_mate/after_mate, mover, POV, current run/line, confidence/provenance. Изменение M5→M3 того же победителя не является CP mistake. В чужих/неполных источниках нельзя выдавать неподтверждённый blunder.
4. Для классификации mate-created/lost ориентироваться на официальные правила Lichess Advice.scala при точном совпадении условий и источника, не выдумывать universal severity из 100000 CP. В остальных случаях «матовая угроза/упущен мат/мат сохранён» как отдельное событие с честной точностью хода. Текущие правила severity по обычному CP не переписывать молча.
5. Существующая SQLite current line_revision/hash, run_id, source provenance, idempotent materialization; additive schema migration только если доказана необходимость, без разрушения личных данных; bounded upgrade старых runs без новой UCI оценки.

### RF6-11 — Review/Progress/UI + обязательные проверки

1. ReviewGraph/список ходов/подробности: показывать точность отдельного хода там, где есть строгая adjacent оценка CP/Mate; степень подтверждения и «неофициально». Отдельно показывать mate M3, M-3, мат уже поставлен, а не искусственную CP. Если позиции до/после разные типы CP/Mate, объяснить динамику вероятности (не «Δ пешек»). Вертикальный AdvantageBar для mate обязан дать осмысленную сторону/долю win%, не пустой результат; изменение ориентации доски учитывается.
2. В Progress выводить отдельный локальный показатель только по подтверждённым scored runs и coverage, не смешивать с official Lichess и с CPL. Не ломать существующие KPI full/partial/n/N/обе стороны. Полный local game accuracy не выдавать из partial run; per-move value допускается для подтверждённой пары с подписью частичного охвата.
3. Тесты: cp 0/±1000; 0→±100 от POV белых и чёрных; parity reference Scala; windows длины 1/2/10/50/100; black-to-move FEN; M5→M3; M3→-M3; M3→CP; mate ноль из Fool's Mate (и зеркально у чёрных); противоречивый mate0; terminal missing engine eval; score white_cp=None when Mate; отсутствие CP-loss при мате; event confidence, stale revision/source, partial scores, depth unavailable; GUI bar/tooltip labels; Fair Play mask before full safety GET, after revoke, human live, 429.
4. Обязательно full pytest (thread warning errors), compileall, git diff --check, native Windows GUI на синтетических mate games, hosted Windows CI на exact final SHA. Реальные PAT/PGN/SQLite/Stockfish и live human не трогать без отдельного разрешения. Не добавлять UCI анализ к открытию Review и не усложнять уже утверждённую страницу «Играть».

## 5. Acceptance и scope

**DONE только если:** (a) Lichess-method calculation воспроизводим и version-pinned; (b) per-move accuracy реально заполнена при CP/Mate паре; (c) terminal и forced mate корректно типизированы, оценка/Bar/описание хода осмысленны без фиктивных CP; (d) local vs official Lichess и coverage прозрачны; (e) fail-closed безопасность не ослаблена; (f) старые RF6-01—RF6-08 тоже закрыты с тестами в том же draft PR #44 и новым exact-HEAD CI. Владелец отдельно утверждает физический UI. **No main merge.**
