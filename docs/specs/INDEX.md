> **07.11.2026 · НЕЗАВИСИМОЕ РЕВЬЮ CODE PR #44 SHA `afb2cc6`: NO GO.** GitHub CI #401 **864 PASS**, но реальные Windows PNG 100/125/150% и source-level audit обнаружили незакрытые требования. **Следующий один корректирующий пакет — [CORR-01…CORR-16](../reviews/ARCH_UX_01_INDEPENDENT_CORRECTIVE_GATE_AFB2CC6_2026_11_07.md)**. Он дополнительно ссылается на полное исходное ТЗ [VISUAL MASTER §15](ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md) (пункт §16 — указатель на ревью). **Не считать визуальный gate пройденным, не merge, не менять main. PARKED navigation audit отдельно.**

> **07.11.2026 · ТЕКУЩЕЕ ОДНО ИСПОЛНИТЕЛЬНОЕ ТЗ:** [VISUAL MASTER §15](ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md). **Весь MP4 + русская речь распознаны** в Descript (899,6 с, SRT с таймкодами); `§15.5` — проверенные кадры VID-01…09, `§15.6` — **голосовые комментарии VOICE-01…10** (сверены с кадрами, source и исходным перечнем без двойных исправлений), `§15.7` — единый Codex-run/gate. Также V-01…03, T-01…06, R-01…05. Голосовое требование **13:30–14:59** сохранить будущий граф переходов отдельно записано как [PARKED navigation-audit](NAVIGATION_PATH_AUDIT_PARKED_2026_11_07.md), **не исполнять сейчас**. Отдельных новых RF/кодовых PR не создавать; docs PR #45, все будущие кодовые исправления только PR #44 с разрешения владельца.

# Chess Coach — карта ТЗ и статусов (07.11.2026)

> **НЕ начинай с древнего `REVIEW-FIX-01` и не читай `main` как текущую реализацию!** Сначала [документ передачи контекста следующему чату](../handoffs/NEW_CHAT_START_HERE_2026_11_07.md). Все указания ниже — **документы** в docs-only draft [PR #45](https://github.com/feshakomissarov-prog/chess/pull/45); код — в [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44). Указанный статус верен на 07.11.2026, после новых коммитов нужно повторное ревью. **Правило одного консолидированного ТЗ**, а не цепочки маленьких RF.

## Где лежит что

| Документ | Статус / назначение | Когда читать |
|---|---|---|
| [Корректирующий независимый CODE+Windows visual gate](../reviews/ARCH_UX_01_INDEPENDENT_CORRECTIVE_GATE_AFB2CC6_2026_11_07.md) | **16 пунктов CORR, NO GO до исправления/тестов/живой приёмки; точный SHA afb2cc6** | Следующему Codex циклу, после распоряжения владельца |
| [NEW_CHAT_START_HERE](../handoffs/NEW_CHAT_START_HERE_2026_11_07.md) | **Точка входа, актуальное состояние, открытые риски, ветки** | Новому чату прежде всего |
| [ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md](ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md) | **Утверждённая цель UI трёх артов и накопленное единое VISUAL MASTER**, в т.ч. §14 VIS6 | При визуальном и функциональном review |
| [ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md](ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) | **Единый полный исходный audit**: 11 CORE, 11 UI, 24 COV, PILOT owner, §13–17 визуальная история | Для прослеживаемости требований и полноты |
| [NAVIGATION_PATH_AUDIT_PARKED_2026_11_07.md](NAVIGATION_PATH_AUDIT_PARKED_2026_11_07.md) | **Отложенный аудит всего графа UI-переходов**, отдельно по просьбе владельца из аудио 13:30–14:59; не запускать сейчас | Только после отдельной команды владельца |
| [ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md) | **Начальное ТЗ ARCH-UX-01**, историческая архитектурно-UX база; расширено MASTER | При проверке исходных контрактов, не как новое задание |
| [LICHESS_STOCKFISH_ACCURACY_MATE_V1.md](LICHESS_STOCKFISH_ACCURACY_MATE_V1.md) | **Методика источников точности/мата**, Lichess vs локальный Stockfish | При ревью метрик/формул |
| [ARCH_UX_01_REVIEW_FIX_01.md](ARCH_UX_01_REVIEW_FIX_01.md) … [ARCH_UX_01_REVIEW_FIX_09_PROGRESS_LIVE_REFRESH_AND_SCOPE.md](ARCH_UX_01_REVIEW_FIX_09_PROGRESS_LIVE_REFRESH_AND_SCOPE.md) | **Исторические исправления RF1–RF9**, интегрированы в последующие комплексные задания. **НЕ запускать повторно** | Только если нужно восстановить конкретный баг/решение |
| [M4_6_DESKTOP_EXPERIENCE.md](M4_6_DESKTOP_EXPERIENCE.md), [M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md](M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md), M0/M3/M4 specs | **Прежние этапы**, большинство уже в main как фундамент | Для продуктовой/кодовой истории |

## Что УТВЕРЖДЕНО владельцем, а что ЕЩЁ НЕТ

**Утверждено:** оболочка шести рабочих разделов и Settings; [UX-B1](../ux/UX_APPROVED_BASELINE_V1.md); [UX-A2](../ux/UX_ANALYSIS_APPROVED_V2.md); [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md); **три конкретных эталонных изображения** Home, My Games, Background — на кодовой ветке в `tests/visual/reference/{01_HOME_APPROVED,02_MY_GAMES_APPROVED,03_BACKGROUND_APPROVED}.png`. Утверждён **визуальный результат**, не цифры synthetic-датасета. Реализацию сравнивать с артом, а не с другим screenshot.

**Исполнено Codex по отчёту, но НЕ принято окончательно владельцем:** VIS6-01…05 на кодовом SHA `5c375b070e6644dbded5fca62d960fc09c90651e`; [CI #368](https://github.com/feshakomissarov-prog/chess/actions/runs/42318755878) 837 PASS, native Windows screenshot artifact. Независимое техническое/визуальное ревью выявило новые проблемы. Не объявлять close/merge.

**Новая единая исполнительная редакция:** проблемы компактного UI/трёх эталонов, Lichess `partial`, preview race и сохранённые R/T замечания дополнены **кадрами Windows-видео (§15.5, VID-01…09) и распознанной русской речью (§15.6, VOICE-01…10)**; общий Codex gate — **§15.7**. Аудио синхронизировано по таймкодам, спорные причины требуют отдельных red tests. Это документ к возможной передаче Codex, а не уже выполненные правки; VIS7/RF не открывать.

**Будущий продуктовый roadmap:** обсуждён новый порядок (единый UI → глубокая аналитика → подготовка к сопернику → обучение на собственных партиях → адаптивный тренер), **пока не утверждён как изменение ROADMAP**. Не путать с ближайшим кодовым scope.

## Как проверить новую итерацию

1. GitHub **точный HEAD PR #44**, чтение **исходников + тестов**, а не только отчёта Codex или PR body.
2. Hosted Windows CI **на этом SHA**; полные тесты, compileall, отсутствие потери Fair Play/PGN/SQLite/Account isolation.
3. Загрузка actual visual artifact; **сравнить три пары** с реальными PNG при 1672×941 и посмотреть заполненные состояния компактных окон/QT scale; screenshot PASS ≠ UI visual PASS.
4. Для пользователя продукт — **подключённый Lichess Windows клиент**, synthetic/offline только внутренние тесты. Не утверждать реальное качество Lichess/Stockfish/physical DPI/Windows audio без live-owner evidence.
5. При **НЕ ОК** — **одно дополнение к действующему §15**, без второго ТЗ. Владелец уже поручил интегрировать видеоревью в PR #45; запуск Codex **не поручен**. Все кодовые исправления — только существующий draft PR #44; ни PR, ни main не сливать без решения владельца.

## Где журнал и матрица

**На КОДОВОЙ ветке PR #44**, а не обязательно в этом docs-only дереве:

- [ARCH_UX_01_EXECUTION_LOG.md](https://github.com/feshakomissarov-prog/chess/blob/5c375b070e6644dbded5fca62d960fc09c90651e/docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md)
- [ARCH_UX_01_MASTER_ACCEPTANCE_MATRIX.md](https://github.com/feshakomissarov-prog/chess/blob/5c375b070e6644dbded5fca62d960fc09c90651e/docs/handoffs/ARCH_UX_01_MASTER_ACCEPTANCE_MATRIX.md)

**Оба SHA в ссылках — фиксация на дату. После следующего Codex-отчёта открывать по новому SHA.**