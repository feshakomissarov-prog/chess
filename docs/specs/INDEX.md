# Chess Coach — карта ТЗ и статусов (07.11.2026)

> **НЕ начинай с древнего `REVIEW-FIX-01` и не читай `main` как текущую реализацию!** Сначала [документ передачи контекста следующему чату](../handoffs/NEW_CHAT_START_HERE_2026_11_07.md). Все указания ниже — **документы** в docs-only draft [PR #45](https://github.com/feshakomissarov-prog/chess/pull/45); код — в [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44). Указанный статус верен на 07.11.2026, после новых коммитов нужно повторное ревью. **Правило одного консолидированного ТЗ**, а не цепочки маленьких RF.

## Где лежит что

| Документ | Статус / назначение | Когда читать |
|---|---|---|
| [NEW_CHAT_START_HERE](../handoffs/NEW_CHAT_START_HERE_2026_11_07.md) | **Точка входа, актуальное состояние, открытые риски, ветки** | Новому чату прежде всего |
| [ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md](ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md) | **Утверждённая цель UI трёх артов и накопленное единое VISUAL MASTER**, в т.ч. §14 VIS6 | При визуальном и функциональном review |
| [ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md](ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) | **Единый полный исходный audit**: 11 CORE, 11 UI, 24 COV, PILOT owner, §13–17 визуальная история | Для прослеживаемости требований и полноты |
| [ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md) | **Начальное ТЗ ARCH-UX-01**, историческая архитектурно-UX база; расширено MASTER | При проверке исходных контрактов, не как новое задание |
| [LICHESS_STOCKFISH_ACCURACY_MATE_V1.md](LICHESS_STOCKFISH_ACCURACY_MATE_V1.md) | **Методика источников точности/мата**, Lichess vs локальный Stockfish | При ревью метрик/формул |
| [ARCH_UX_01_REVIEW_FIX_01.md](ARCH_UX_01_REVIEW_FIX_01.md) … [ARCH_UX_01_REVIEW_FIX_09_PROGRESS_LIVE_REFRESH_AND_SCOPE.md](ARCH_UX_01_REVIEW_FIX_09_PROGRESS_LIVE_REFRESH_AND_SCOPE.md) | **Исторические исправления RF1–RF9**, интегрированы в последующие комплексные задания. **НЕ запускать повторно** | Только если нужно восстановить конкретный баг/решение |
| [M4_6_DESKTOP_EXPERIENCE.md](M4_6_DESKTOP_EXPERIENCE.md), [M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md](M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md), M0/M3/M4 specs | **Прежние этапы**, большинство уже в main как фундамент | Для продуктовой/кодовой истории |

## Что УТВЕРЖДЕНО владельцем, а что ЕЩЁ НЕТ

**Утверждено:** оболочка шести рабочих разделов и Settings; [UX-B1](../ux/UX_APPROVED_BASELINE_V1.md); [UX-A2](../ux/UX_ANALYSIS_APPROVED_V2.md); [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md); **три конкретных эталонных изображения** Home, My Games, Background — на кодовой ветке в `tests/visual/reference/{01_HOME_APPROVED,02_MY_GAMES_APPROVED,03_BACKGROUND_APPROVED}.png`. Утверждён **визуальный результат**, не цифры synthetic-датасета. Реализацию сравнивать с артом, а не с другим screenshot.

**Исполнено Codex по отчёту, но НЕ принято окончательно владельцем:** VIS6-01…05 на кодовом SHA `5c375b070e6644dbded5fca62d960fc09c90651e`; [CI #368](https://github.com/feshakomissarov-prog/chess/actions/runs/42318755878) 837 PASS, native Windows screenshot artifact. Независимое техническое/визуальное ревью выявило новые проблемы. Не объявлять close/merge.

**В стадии обсуждения, НЕ кодовое ТЗ:** проблемы компактного Qt 125/150% (обрезание и наложения), fidelity трёх эталонов, `partial` в Lichess counters, race `handle_ref[0]` в preview, длительность на реальной SQLite со множеством анализов; подробности в [handoff §4](../handoffs/NEW_CHAT_START_HERE_2026_11_07.md). **Не создавать исполняемый VIS7/новый RF без согласования с владельцем**, не публиковать отдельный MD-файл исправлений раньше обсуждения.

**Будущий продуктовый roadmap:** обсуждён новый порядок (единый UI → глубокая аналитика → подготовка к сопернику → обучение на собственных партиях → адаптивный тренер), **пока не утверждён как изменение ROADMAP**. Не путать с ближайшим кодовым scope.

## Как проверить новую итерацию

1. GitHub **точный HEAD PR #44**, чтение **исходников + тестов**, а не только отчёта Codex или PR body.
2. Hosted Windows CI **на этом SHA**; полные тесты, compileall, отсутствие потери Fair Play/PGN/SQLite/Account isolation.
3. Загрузка actual visual artifact; **сравнить три пары** с реальными PNG при 1672×941 и посмотреть заполненные состояния компактных окон/QT scale; screenshot PASS ≠ UI visual PASS.
4. Для пользователя продукт — **подключённый Lichess Windows клиент**, synthetic/offline только внутренние тесты. Не утверждать реальное качество Lichess/Stockfish/physical DPI/Windows audio без live-owner evidence.
5. При **НЕ ОК** — **один сводный пакет** и сначала согласование с владельцем; только по его команде редактировать активное ТЗ в PR #45. Все кодовые исправления — Codex **в существующем draft PR #44**; ни PR, ни main самостоятельно не сливать.

## Где журнал и матрица

**На КОДОВОЙ ветке PR #44**, а не обязательно в этом docs-only дереве:

- [ARCH_UX_01_EXECUTION_LOG.md](https://github.com/feshakomissarov-prog/chess/blob/5c375b070e6644dbded5fca62d960fc09c90651e/docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md)
- [ARCH_UX_01_MASTER_ACCEPTANCE_MATRIX.md](https://github.com/feshakomissarov-prog/chess/blob/5c375b070e6644dbded5fca62d960fc09c90651e/docs/handoffs/ARCH_UX_01_MASTER_ACCEPTANCE_MATRIX.md)

**Оба SHA в ссылках — фиксация на дату. После следующего Codex-отчёта открывать по новому SHA.**