# Покрытие русских названий дебютов

Отчёт сформирован локально по bundled TSV reference catalog из `src/chess_coach/opening_data/`. PGN и SQLite не переписываются; перевод применяется только при отображении.

- Строк каталога: 3865
- Уникальных пар ECO + английское имя: 3332
- Отнесено к русской семье или точному имени: 3312 (99.4%)
- Оставлено на честном fallback `ECO · дебют не классифицирован`: 20 (0.6%)
- Английское исходное имя сохранено для tooltip/details.
- Неизвестные D00–D05 не переименовываются в Лондонскую систему только по ECO.

## Неразрешённые названия

Они намеренно остаются нейтральным fallback до проверки шахматного соответствия:

- `A00` — Amsterdam Attack
- `A00` — Creepy Crawly Formation: Classical Defense
- `A00` — Formation: Shy Attack
- `A00` — Global Opening
- `A00` — Lasker Simul Special
- `A00` — Valencia Opening
- `A40` — Borg Defense: Borg Gambit
- `A40` — Montevideo Defense
- `A40` — Zaire Defense
- `A45` — Basque Opening
- `A45` — Canard Opening
- `A45` — Paleface Attack
- `A46` — Döry Defense
- `B00` — Borg Defense
- `B00` — Borg Defense: Borg Gambit
- `B00` — Borg Defense: Troon Gambit
- `B00` — Borg Defense: Zilbermints Gambit
- `B00` — Fried Fox Defense
- `B00` — Lemming Defense
- `C40` — Gunderam Defense
