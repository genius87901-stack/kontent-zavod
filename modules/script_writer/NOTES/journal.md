# Журнал модуля script_writer (BOX-13)

Дописываемый лог для устойчивости к обрывам. Если сессия оборвётся — читать
отсюда и продолжать.

## Задача
Модуль `modules/script_writer/` — генерация СЦЕНАРИЯ СЪЁМКИ под один разговорный
Reels: список дублей (что сказать + что показать по кадрам), тип плана,
длительность, рекомендованная раскладка (split/cutout/pip/card/lower-third/
text-behind/demo/compare), хук (1–5 сек) и CTA с кодовым словом. Детерминированно.
Образец структуры — `modules/content_plan/`. LLM-хук — заглушка как
`planner.llm_ideas` / `caption_bridge.llm_caption` (по умолчанию сеть/платный API
НЕ зовёт).

## Разведка (сделано)
- Прочитал CONTRACT.md (§3 запреты, §5 реестр), ПРОМТ_КОНТЕНТ.md (ПРОМТ 2 —
  основа), ФОРМАТЫ_РАЗГОВОРНЫХ_REELS.md, CONTENT_BIBLE_Valeria_Chirkova.md.
- Изучил эталон content_plan: manifest, doctor, config, paths, model, planner
  (llm_ideas-хук), caption_bridge (llm_caption-хук + load_bible), cli, questions,
  DESIGN, .gitignore.
- Валидный список раскладок подтверждён из engine_talk.py (grep template==):
  split, cutout, pip, card, lower-third, text-behind, demo, compare.
- Таксономия intent из talk_plan (через reels_bridge): hook / point / emotion / cta.
- Питон на этой машине 3.9.6 → пишу код, совместимый с 3.9 (как content_plan:
  `from __future__ import annotations`, без 3.10+ синтаксиса), манифест
  python_requires ">=3.11" как у content_plan.
- Детерминизм: НЕ использую builtin hash() (он рандомизирован PYTHONHASHSEED) —
  беру устойчивый hashlib.md5. Это надёжнее, чем в caption_bridge.

## Архитектура арки сценария (Боль→Ошибка→Кейс→Инсайт→Возможность→CTA)
Из шаблона Content Bible «30 сценариев прогрева» и ПРОМТ 2. Дубли:
ХУК → [pain, mistake, case, insight, (opportunity)] → CTA. Кол-во средних битов
3–5 (по умолчанию 4). Раскладка/план/intent выводятся из стадии бита + рубрики
детерминированными правилами.

## Файлы (план)
__init__.py, config.py, paths.py, model.py, bible.py, writer.py, render.py,
doctor.py, cli.py, questions.py, module.manifest.json, .gitignore,
brief.example.json, DESIGN.md, NOTES/journal.md.

## Статус (ЗАВЕРШЕНО)
- [x] Разведка
- [x] journal.md
- [x] код модуля: __init__, config, paths, model, bible, writer, render,
      doctor, cli, questions, manifest, brief.example.json, DESIGN.md
- [x] doctor rc=0 (проверено)
- [x] пример сценария на книге смыслов (Точки роста / 500 000 ₽) → .md/.txt/.json
- [x] grep-линт запретов чист (только строки-комментарии про запрет)
- [x] DESIGN.md + предложенная строка реестра §5
- [x] детерминизм подтверждён кросс-процессно (PYTHONHASHSEED 0 vs 12345 → идентично)
- [x] покрытие всех 8 раскладок, валидность 7 рубрик × 3/4/5 битов, сумма
      длительностей == цель, LLM-хук: база/merge/фолбэк

## Что осталось хвостом (для ведущего)
- Строку реестра §5 в CONTRACT.md вписывает ведущий (текст — в DESIGN.md §7).
- Живой LLM-хук (writer.llm_script) — заглушка по контракту; подключение ~8–12 ч
  (разбивка в DESIGN.md §5). Отдельная задача, платный API согласовать.
- Не коммитил/не пушил/не трогал BOARD.md — по инструкции. board-команда и
  git-действия — за ведущим.
- Рантайм-папка script_writer/ в корне worktree создаётся при прогоне и
  само-гитигнорится (`*`); в git не попадает (проверено check-ignore).

## Итерация 2 — упрощение вывода + 3 рычага качества (по просьбе человека)
Задача: убрать лишнее из вывода и поднять качество БЕЗ платного ИИ.

**1. Упрощён дубль.** Из сценария/дубля убраны поля `layout` (раскладка монтажа) и
`shot_type` (тип плана). Дубль теперь: `say` / `show` / `duration_sec` /
`subtitle` (+ служебные `intent`, `visual_query`). Обновлены render (.txt/.md),
JSON (через build_script), model.validate (больше не требует layout/shot_type),
doctor, cli (убрана подкоманда `formats` и `model.LAYOUTS`/`SHOT_*`/`LAYOUT_HUMAN`),
manifest, __init__, DESIGN. `SCHEMA_VERSION` 1→2. Артефакты чисты от раскладок
(grep). doctor rc=0.

**2. Три рычага (всё офлайн, детерминированно, мягкий фолбэк):**
- Левер 1 — реальные фразы книги: `bible.py` парсит «100 ТЕМ ДЛЯ ПОСТОВ» →
  `themes` {рубрика→[фразы]}; идут в пул хука и в биты `ошибка`/`возможность`
  «его голосом»; кейс — на якоре «136 точек роста». Фикс: короткий маркер «ии»
  матчил «провокацИИ» → матч коротких (<=3) маркеров только по полному совпадению.
- Левер 2 — виральные хуки: `writer._niche_hooks()` читает готовый банк через
  `content_plan.niche_bridge.load_triples` (только чтение; нет банка → [], без
  сети). Через `build_script(..., niche_source=...)`.
- Левер 3 — ротация арок: банк `_ARCS` (5 порядков средних битов), выбор по md5
  темы (`_pick_arc`); `case` в первых 3 позициях каждой арки → инвариант
  хук→…→кейс→CTA. Поле `arc` в сценарии.

**Замер:** doctor rc=0; детерминизм (одинаковый вход → одинаковый .json без
`generated` и одинаковый .txt) — подтверждён; арки различаются по темам (все 5 в
ротации на 10 темах); grep-линт: нет builtin hash()/input()/сети/секретов/абс.путей
(совпадения только в комментариях-запретах); py_compile OK. Платный API не звал,
не коммитил, ничего не публиковал. Правки только в `modules/script_writer/`.
