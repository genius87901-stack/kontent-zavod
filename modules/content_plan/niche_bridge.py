# -*- coding: utf-8 -*-
"""ШОВ ВИРАЛЬНОЙ РАЗВЕДКИ → КНИГА СМЫСЛОВ ПЛАНИРОВЩИКА.

Зачем: скилл `/niche-hooks` (ручная конкурентная разведка) кладёт на диск банк
хуков — markdown-файл с реально выстрелившими первыми фразами и темами ниши,
привязанными к цифрам (см. `skills/niche-hooks/SKILL.md`, раздел «Выход»). Этот
модуль читает УЖЕ ПРОИЗВЕДЁННЫЕ артефакты разведки и превращает их в тройки
`(topic, hook, meaning)`, пригодные планировщику (`planner.py`, `TOPIC_BANK` /
`llm_ideas`). Так виральная разведка кормит генерацию идей, не встраиваясь в
авто-пайплайн.

ГРАНИЦЫ (соблюдать строго):
  * НЕ запускает разведку и НЕ зовёт vidIQ / платные API — читает только готовый
    .md-банк с диска. Нет банка → пустой результат, а не падение.
  * Детерминированно: тот же вход → тот же выход (файлы сортируются по имени,
    порядок внутри файла сохраняется, дедуп стабилен). Никакого `hash()`
    (рандомизирован PYTHONHASHSEED) и никакой сети.
  * Пути — только относительно корня установки (каталог с CONTRACT.md) через
    pathlib; никаких абсолютных путей, `input()` и секретов.
  * Сам `planner.py` этот модуль НЕ правит — даёт функцию-точку, которую
    планировщик СМОГ БЫ вызвать. См. блок «ИНТЕГРАЦИЯ С planner» ниже.

--------------------------------------------------------------------------------
ИНТЕГРАЦИЯ С planner (описание, без правки planner сейчас)
--------------------------------------------------------------------------------
`planner._build_idea_pool(rubrics, brief, generate=None)` уже принимает `generate`
и прокидывает его в `planner.llm_ideas(rubric, brief, base_pool, generate=...)`.
Контракт `generate`: `generate(rubric, brief, base_pool) -> list[triple]` — вернуть
дополнительные тройки `(topic, hook, meaning)`; planner сам отбросит дубликаты тем
и мусор. Наш `make_planner_generate()` возвращает ровно такой вызываемый объект.

Чтобы включить банк ниши, в `planner.build_plan` строку

    idea_pool = _build_idea_pool(rubrics, b)

достаточно заменить (в ОТДЕЛЬНОЙ задаче, владельцем content_plan) на

    from . import niche_bridge
    idea_pool = _build_idea_pool(rubrics, b,
                                 generate=niche_bridge.make_planner_generate())

Никакой сети в планировщике при этом не появляется: `make_planner_generate`
читает только локальный .md-банк. Нет банка → generate вернёт [] и план
останется прежним (чистая детерминированная база).

Альтернатива без плаг-ина в `llm_ideas` — `extend_topic_bank(planner.TOPIC_BANK)`
отдаёт НОВЫЙ словарь-банк с подмешанными тройками ниши (planner.TOPIC_BANK не
мутируется).
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import install_root

# Куда `/niche-hooks` кладёт банк (SKILL.md: research/niche-hooks/<slug>_<дата>.md).
# Путь относительно корня установки — резолвим через install_root().
NICHE_DIR = "research/niche-hooks"

# Заголовки разделов банка (SKILL.md «Выход»). Матчим по нормализованной
# подстроке, чтобы пережить мелкие вариации нумерации/оформления заголовка.
_SECTION_HOOKS = "БАНК ХУКОВ"
_SECTION_THEMES = "ТЕМЫ С ОТКЛИКОМ"

# Токен ссылки в строке банка.
_URL_RE = re.compile(r"https?://\S+")
# Метрика — первая скобка (…) в строке хука.
_METRIC_RE = re.compile(r"\(([^()]*)\)")
# Маркер списка в начале строки: «- », «* », «1. », «1) », «•».
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# Заголовок markdown.
_HEADER_RE = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")
# Кавычки-ёлочки / прямые / типографские вокруг дословной фразы.
_QUOTED_RE = re.compile(r"[«\"“]([^»\"”]+)[»\"”]")
_TERMINAL_PUNCT = " \t.!?…»\"”'’`*_"


# ---------------------------------------------------------------------------
# Разбор одного .md-банка
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Ключ для дедупа: без регистра и лишних пробелов. Оригинал не меняем."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _strip_terminal(text: str) -> str:
    """Тема из хука: снять хвостовую пунктуацию/оформление, не трогая середину."""
    return (text or "").strip().strip(_TERMINAL_PUNCT).strip()


def _clean_inline(text: str) -> str:
    """Снять markdown-обвязку (жирный/курсив, ведущий маркер) со строки."""
    text = _BULLET_RE.sub("", text or "")
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def _parse_hook_line(line: str):
    """Строка «БАНК ХУКОВ» → (phrase, metric). Дословную фразу берём из кавычек,
    иначе — из текста до метрики/ссылки. Пусто → None."""
    raw = _clean_inline(line)
    if not raw:
        return None
    metric = ""
    m = _METRIC_RE.search(raw)
    if m:
        metric = _normalize_metric(m.group(1))
    quoted = _QUOTED_RE.search(raw)
    if quoted:
        phrase = quoted.group(1).strip()
    else:
        # до первой скобки / разделителя тире / ссылки
        phrase = raw
        phrase = _URL_RE.sub("", phrase)
        phrase = re.split(r"[(—]|\s[-–]\s", phrase, maxsplit=1)[0]
        phrase = phrase.strip()
    phrase = phrase.strip(_TERMINAL_PUNCT + "—–-").strip()
    # вернуть дословную фразу как хук — с её собственной концевой точкой, если была
    hook = _restore_hook(quoted.group(1) if quoted else phrase)
    if not phrase:
        return None
    return (hook, metric)


def _restore_hook(text: str) -> str:
    """Хук — дословная первая фраза. Оставляем как есть, только режем обрамление."""
    return (text or "").strip().strip("«»\"“”*_ ").strip()


def _normalize_metric(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _parse_theme_line(line: str):
    """Строка «ТЕМЫ С ОТКЛИКОМ» → тема (без метрик и ссылок). Пусто → None."""
    raw = _clean_inline(line)
    if not raw:
        return None
    raw = _URL_RE.sub("", raw)
    raw = _METRIC_RE.sub("", raw)
    # тема часто идёт «Заголовок — пояснение»: берём часть до тире как тему
    raw = re.split(r"\s[—–]\s|:\s", raw, maxsplit=1)[0]
    theme = raw.strip(_TERMINAL_PUNCT + "—–-").strip()
    return theme or None


def _iter_sections(text: str):
    """Разбить markdown на (header_text, [строки тела]) по заголовкам #…######."""
    header = None
    body = []
    for line in (text or "").splitlines():
        h = _HEADER_RE.match(line)
        if h:
            if header is not None:
                yield header, body
            header = h.group(1)
            body = []
        else:
            body.append(line)
    if header is not None:
        yield header, body


def _slug_from_name(path: Path) -> str:
    """Ниша из имени файла <slug>_<ГГГГ-ММ-ДД>.md → человекочитаемый slug."""
    stem = path.stem
    stem = re.sub(r"[_-]?\d{4}-\d{2}-\d{2}$", "", stem)
    return stem.replace("-", " ").replace("_", " ").strip() or path.stem


def parse_bank_text(text: str, niche: str = "") -> dict:
    """Разобрать содержимое одного .md-банка в {niche, hooks, themes}.

    hooks  — список (phrase, metric) в порядке файла;
    themes — список строк-тем в порядке файла.
    Толерантно к оформлению: неизвестные разделы игнорируются, кривые строки
    пропускаются, а не роняют разбор."""
    hooks = []
    themes = []
    for header, body in _iter_sections(text):
        hkey = _normalize(header)
        is_hooks = _normalize(_SECTION_HOOKS) in hkey
        is_themes = _normalize(_SECTION_THEMES) in hkey
        if not (is_hooks or is_themes):
            continue
        for line in body:
            if not _BULLET_RE.match(line):
                continue
            if is_hooks:
                parsed = _parse_hook_line(line)
                if parsed and parsed[0]:
                    hooks.append(parsed)
            else:
                theme = _parse_theme_line(line)
                if theme:
                    themes.append(theme)
    return {"niche": niche, "hooks": hooks, "themes": themes}


# ---------------------------------------------------------------------------
# Загрузка банков с диска (мягкое отсутствие входа)
# ---------------------------------------------------------------------------

def _resolve_source(source=None):
    """Отдаёт список .md-файлов банка. source: None → NICHE_DIR под корнем;
    каталог → его *.md; файл → он один. Нет входа → []. Никогда не падает на
    отсутствии каталога/файла."""
    if source is None:
        base = install_root() / NICHE_DIR
    else:
        base = Path(source)
        if not base.is_absolute():
            base = install_root() / base
    try:
        if base.is_dir():
            return sorted(base.glob("*.md"), key=lambda p: p.name)
        if base.is_file():
            return [base]
    except OSError:
        return []
    return []


def load_banks(source=None) -> list:
    """Прочитать все .md-банки из source. Возвращает список {niche, hooks, themes}
    в детерминированном порядке (файлы по имени). Нет банка → []."""
    banks = []
    for path in _resolve_source(source):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        banks.append(parse_bank_text(text, niche=_slug_from_name(path)))
    return banks


# ---------------------------------------------------------------------------
# Банк → тройки планировщика (topic, hook, meaning)
# ---------------------------------------------------------------------------

def banks_to_triples(banks) -> list:
    """Свести банки в детерминированный дедуплицированный список троек
    `(topic, hook, meaning)` для планировщика.

    Правила сборки:
      * из «БАНК ХУКОВ» — хук берём дословно; тема = хук без хвостовой пунктуации;
        смысл несёт происхождение и цифру: «виральный хук ниши «X» (метрика)» —
        честно помечает, что это сырьё разведки, а не финальный смысл;
      * из «ТЕМЫ С ОТКЛИКОМ» — тема как есть; хук пустой (планировщик подставит
        свой); смысл: «тема с откликом в нише «X»».
    Дедуп по НОРМАЛИЗОВАННОЙ теме, первое вхождение побеждает; порядок стабилен
    (сначала все хук-тройки в порядке файлов, затем тема-тройки)."""
    triples = []
    seen = set()

    def _add(topic, hook, meaning):
        topic = (topic or "").strip()
        if not topic:
            return
        key = _normalize(topic)
        if key in seen:
            return
        seen.add(key)
        triples.append((topic, (hook or "").strip(), (meaning or "").strip()))

    for bank in banks or []:
        niche = bank.get("niche") or "разведка ниши"
        for hook, metric in bank.get("hooks") or []:
            topic = _strip_terminal(hook)
            tail = " (%s)" % metric if metric else ""
            meaning = "виральный хук ниши «%s»%s" % (niche, tail)
            _add(topic, hook, meaning)
    for bank in banks or []:
        niche = bank.get("niche") or "разведка ниши"
        for theme in bank.get("themes") or []:
            meaning = "тема с откликом в нише «%s»" % niche
            _add(theme, "", meaning)
    return triples


def load_triples(source=None) -> list:
    """Удобный фасад: диск → дедуплицированные тройки. Нет банка → []."""
    return banks_to_triples(load_banks(source))


# ---------------------------------------------------------------------------
# Точки интеграции с планировщиком (planner сам НЕ правим)
# ---------------------------------------------------------------------------

def make_planner_generate(source=None, target_rubric=None):
    """Собрать вызываемый объект под контракт `planner.llm_ideas`:
    `generate(rubric, brief, base_pool) -> list[triple]`.

    Тройки ниши раскладываются по рубрикам детерминированно:
      * `target_rubric` задан → ВСЕ тройки идут в эту рубрику, в остальные — [];
      * иначе — round-robin по списку рубрик из `brief["rubrics"]` (planner
        передаёт нормализованный brief), тройка i → рубрике i % len(rubrics).
    Так один и тот же хук не дублируется в каждую рубрику. Банк читается один
    раз и кэшируется в замыкании — детерминизм и без лишних чтений диска.

    Планировщик сам отбросит дубликаты тем относительно своей базы и мусор, так
    что здесь дополнительная фильтрация не нужна."""
    cached = load_triples(source)

    def generate(rubric, brief, base_pool):
        if not cached:
            return []
        if target_rubric is not None:
            return list(cached) if rubric == target_rubric else []
        rubrics = list((brief or {}).get("rubrics") or [])
        if not rubrics or rubric not in rubrics:
            # список рубрик неизвестен — не гадаем, ничего не подмешиваем
            return []
        idx = rubrics.index(rubric)
        n = len(rubrics)
        return [t for i, t in enumerate(cached) if i % n == idx]

    return generate


def extend_topic_bank(topic_bank, source=None, target_rubric=None) -> dict:
    """Вернуть НОВЫЙ банк рубрик (копию `topic_bank`) с подмешанными тройками
    ниши, без повтора тем внутри рубрики. Исходный словарь НЕ мутируется.

    Раскладка по рубрикам — как в `make_planner_generate` (round-robin или в
    `target_rubric`). Полезно для интеграции, не трогающей `llm_ideas`."""
    rubrics = list(topic_bank.keys())
    merged = {r: list(v) for r, v in topic_bank.items()}
    if not rubrics:
        return merged
    triples = load_triples(source)
    for i, triple in enumerate(triples):
        if target_rubric is not None:
            rubric = target_rubric
        else:
            rubric = rubrics[i % len(rubrics)]
        bucket = merged.setdefault(rubric, [])
        seen = {_normalize(t[0]) for t in bucket}
        if _normalize(triple[0]) not in seen:
            bucket.append(triple)
    return merged
