# -*- coding: utf-8 -*-
"""Планировщик: из brief собирает контент-план (расписание слотов).

Детерминированный (без случайности и без сети) — тесты стабильны. Опирается на
рубрики Content Bible клиента (CONTENT_BIBLE_Valeria_Chirkova.md: 100 тем + банк
хуков): под каждую рубрику держим пул тема/хук/смысл, планировщик крутит их
ротацией БЕЗ повтора темы И хука в окне плана, равномерно чередуя рубрики; под
ключевые даты брифа ставит отдельные приоритетные слоты.

«Умное» расширение банка идей задумано как LLM-шаг, но платный API здесь НЕ
зовётся: база (пул тем из книги смыслов + правила ротации) уже даёт непустой
неповторяющийся план, а точка расширения вынесена в явный хук `llm_ideas(...)`
(см. ниже) — туда позже можно подать генератор от модели, ничего в планировщике
не меняя.

Ничего не публикует и не генерит — только раскладывает план.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config, model, topics

# --- банк смыслов по рубрикам ------------------------------------------------
# BUG-2026-08-08: раньше банк был захардкожен здесь под книгу смыслов Валерии
# и не был переключаем per-client. Теперь источник — topics.load_topic_bank():
# рубрики клиента (если в его книге смыслов есть секция тем) приоритетны, а
# заготовка ниже (topic_bank.default.json) — фолбэк для рубрик, которых у
# клиента нет. TOPIC_BANK ниже — застывший снимок ЗАГОТОВКИ (пример-донор),
# оставлен как есть только для обратной совместимости кода, который может
# импортировать `planner.TOPIC_BANK` напрямую (см. niche_bridge.py, docstring).
# Реальный рабочий банк, которым пользуется планировщик — `_TOPIC_BANK` ниже.
#
# Каждый элемент — тройка (topic, hook, meaning):
#   topic   — тема поста,
#   hook    — первая фраза / обложка,
#   meaning — что доносим клиенту этим постом.
# ⚠️ Литерал банка отсюда УБРАН. Он оставался «застывшим снимком для обратной
# совместимости» — и вместе с ним оставались темы одного эксперта («дополнительные
# 500 000 ₽», «136 точек роста»), а DEFAULT_RUBRICS ниже брал ИМЕНА его рубрик.
# То есть у клиента другой ниши порядок рубрик по умолчанию всё равно приезжал
# чужим. Источник теперь один — нейтральная заготовка topic_bank.default.json,
# которую читает topics.py; двух источников правды у банка больше нет.
TOPIC_BANK = topics.default_bank()


def llm_ideas(rubric: str, brief: dict, base_pool: list, generate=None) -> list:
    """Точка расширения пула идей рубрики «умным» шагом (LLM).

    КОНТРАКТ РЕЛЬСОВ: сама функция НЕ ходит в сеть и НЕ зовёт платный API. По
    умолчанию возвращает детерминированную базу (`base_pool`) как есть — этого
    уже достаточно для непустого неповторяющегося плана.

    Расширение включается только если ВЕДУЩИЙ явно передаёт `generate` —
    вызываемый объект `generate(rubric, brief, base_pool) -> list[triple]`,
    который где-то снаружи (в отдельной задаче) обратится к модели и вернёт
    дополнительные тройки (topic, hook, meaning). Здесь мы лишь аккуратно
    подмешиваем их к базе, отбрасывая дубликаты тем и мусор. Никакого сетевого
    кода в планировщике нет — только этот безопасный шов.
    """
    pool = list(base_pool or [])
    if generate is None:
        return pool
    try:
        extra = generate(rubric, brief, pool) or []
    except Exception:
        # расширение — best-effort надстройка; сбой модели не роняет план
        return pool
    seen_topics = {t[0] for t in pool}
    for item in extra:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        topic, hook, meaning = str(item[0]), str(item[1]), str(item[2])
        if topic and topic not in seen_topics:
            seen_topics.add(topic)
            pool.append((topic, hook, meaning))
    return pool


def _build_idea_pool(rubrics, brief: dict, bank: dict, generate=None) -> dict:
    """Собирает рабочий банк идей по рубрикам: база (клиентская или дефолтная,
    см. topics.load_topic_bank) + (опц.) LLM-расширение.

    `generate` по умолчанию None → чистая детерминированная база. Передаётся
    только явно (см. llm_ideas) — планировщик сам модель не зовёт.
    """
    pool = {}
    for rubric in rubrics:
        base = bank.get(rubric) or []
        pool[rubric] = llm_ideas(rubric, brief, base, generate=generate)
    return pool

# Порядок рубрик по умолчанию — из заготовки-донора (topic_bank.default.json).
# Реальный рабочий список рубрик каждого прогона плана — из бриф-ответа клиента
# / актуального банка (см. build_plan → topics.load_topic_bank), не отсюда.
DEFAULT_RUBRICS = list(TOPIC_BANK.keys())

# Раскладка «сколько постов в неделю» → в какие дни недели (0=Пн … 6=Вс).
WEEKDAY_MAP = {
    1: [1],
    2: [1, 4],
    3: [0, 2, 4],
    4: [0, 1, 3, 5],
    5: [0, 1, 2, 3, 4],
    6: [0, 1, 2, 3, 4, 5],
    7: [0, 1, 2, 3, 4, 5, 6],
}


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _posting_days(start: date, end: date, per_week: int):
    per_week = max(1, min(7, int(per_week)))
    weekdays = WEEKDAY_MAP[per_week]
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() in weekdays:
            days.append(cur)
        cur += timedelta(days=1)
    return days


# Форматы, которые планировщик умеет ставить в план. ВСЕ ТРИ собираются движками
# завода по-настоящему: carousel → carousel_bridge, reels → reels_bridge,
# stories → stories_bridge (28.08.2026; до этого сторис планировались, но не
# собирались — движок был, шва не было, см. executor._real_media_for).
_KNOWN_FORMATS = ("carousel", "reels", "stories")


def _coerce_format_list(value):
    """Приводит форматы к списку известных (carousel/reels/stories): список или
    строка через запятую → нормализованный список; мусор → []."""
    if isinstance(value, (list, tuple)):
        raw = [str(x).strip().lower() for x in value]
    elif isinstance(value, str):
        raw = [x.strip().lower() for x in value.split(",")]
    else:
        return []
    return [f for f in raw if f in _KNOWN_FORMATS]


def _resolve_platform_volume(b: dict):
    """Кол-во постов/нед НА КАЖДУЮ площадку или None (тогда общий posts_per_week).

    Приоритет: явный ключ brief `per_platform_volume` → умолчание из конфига
    (`plan_per_platform_volume`). Что не указано для выбранной площадки —
    падает на общий `posts_per_week` (обратная совместимость по площадкам).
    Нет ни того ни другого → None: планировщик работает как раньше."""
    if "per_platform_volume" in b:
        coerced = config.parse_platform_volume(b.get("per_platform_volume"))
    else:
        coerced = config.default_per_platform_volume()
    if not coerced:
        return None
    fallback = max(1, int(b["posts_per_week"]))
    platforms = b["platforms"] or list(coerced.keys())
    return {plat: int(coerced.get(plat, fallback)) for plat in platforms}


def _resolve_platform_formats(b: dict) -> dict:
    """Форматы, закреплённые за площадкой (`per_platform_formats`). Пусто/нет →
    {} (тогда для площадки берётся общий список форматов брифа)."""
    raw = b.get("per_platform_formats")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        plat = str(k).strip().lower()
        fmts = _coerce_format_list(v)
        if plat and fmts:
            out[plat] = fmts
    return out


def aware_iso(publish_at: str, tz_name: str):
    """«2026-08-05T09:00:00» + зона → «2026-08-05T09:00:00+03:00».
    Битую дату не раздуваем в исключение: вернём None, доктор это поймает."""
    try:
        naive = datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=config.tzinfo_for(tz_name)).isoformat()


def _norm_brief(brief: dict, bank: dict) -> dict:
    """Заполняет умолчания из конфига там, где клиент не ответил.

    `bank` — актуальный банк тем (topics.load_topic_bank): рубрики клиента +
    заготовка-донор на недостающее. Валидируем `rubrics` брифа против НЕГО, а
    не против статичного TOPIC_BANK — иначе рубрики клиента другой ниши
    (BUG-2026-08-08) отсеивались бы здесь же, ещё до сборки плана."""
    b = dict(brief or {})
    b.setdefault("platforms", config.default_platforms())
    b.setdefault("posts_per_week", config.posts_per_week())
    b.setdefault("horizon_days", config.plan_horizon_days())
    b.setdefault("veto_hours", config.veto_hours())
    b.setdefault("formats", ["carousel", "reels"])
    b.setdefault("post_time", config.default_post_time())
    b.setdefault("post_timezone", config.default_timezone())
    # Вопросы про время и зону — choice_or_text: клиент вправе назвать своё.
    # Приводим к канону здесь, чтобы дальше по коду формат был один.
    b["post_time"] = config.normalize_time(b.get("post_time"))
    b["post_timezone"] = config.normalize_timezone(b.get("post_timezone"))
    b.setdefault("tone", "на «ты», мужской род, конкретика, без инфоцыганщины")
    b.setdefault("forbidden", [])
    b.setdefault("key_dates", [])
    bank_rubrics = list(bank.keys()) or DEFAULT_RUBRICS
    rubrics = b.get("rubrics") or bank_rubrics
    b["rubrics"] = [r for r in rubrics if r in bank] or bank_rubrics

    period = b.get("period") or {}
    if not period.get("start"):
        period["start"] = date.today().isoformat()
    start = _parse_date(period["start"])
    if not period.get("end"):
        period["end"] = (start + timedelta(days=int(b["horizon_days"]) - 1)).isoformat()
    b["period"] = period
    return b


def _pick_topic(rubric, pool, cursors, used_topics, used_hooks):
    """Следующая идея рубрики: круговая ротация с курсором + запрет повтора темы
    И хука в окне плана.

    Три прохода от текущего курсора рубрики:
      1) тема И хук ещё не встречались в окне (идеальный случай);
      2) хотя бы тема не встречалась (хук может повториться, если пул хуков
         исчерпан раньше тем);
      3) пул рубрики полностью исчерпан в окне — отдаём по курсору (наименее
         недавнюю), чтобы не липнуть к первой теме.
    Курсор двигается в любом случае → равномерный обход банка рубрики.
    """
    bank = pool.get(rubric) or []
    n = len(bank)
    if not n:
        return ("Тема из рубрики «%s»" % rubric, "", "")
    start = cursors.get(rubric, 0)

    for offset in range(n):
        idx = (start + offset) % n
        topic, hook, meaning = bank[idx]
        if topic not in used_topics and hook not in used_hooks:
            cursors[rubric] = idx + 1
            used_topics.add(topic)
            used_hooks.add(hook)
            return bank[idx]

    for offset in range(n):
        idx = (start + offset) % n
        topic, hook, meaning = bank[idx]
        if topic not in used_topics:
            cursors[rubric] = idx + 1
            used_topics.add(topic)
            used_hooks.add(hook)
            return bank[idx]

    idx = start % n
    cursors[rubric] = idx + 1
    return bank[idx]


def _build_events(b: dict, start: date, end: date, key_by_date: dict,
                  per_platform_volume, per_platform_formats: dict):
    """Список «событий публикации» — из чего планировщик лепит слоты.

    Каждое событие: {"date", "platforms", "formats", "key", "per_platform"}.

    ДВА режима:
      * per_platform_volume == None — ОБРАТНАЯ СОВМЕСТИМОСТЬ: один слот в день
        на ВСЕ площадки по общему posts_per_week (прежнее поведение, байт-в-байт).
      * per_platform_volume задан — у КАЖДОЙ площадки своя частота: для каждой
        строим свои дни публикации и своё событие (одна площадка на слот).
        Форматы берём из per_platform_formats[площадка] или из общего списка.
    """
    global_formats = b["formats"] or ["carousel"]
    all_platforms = list(b["platforms"])

    if not per_platform_volume:
        # --- прежний путь: не меняем порядок и раскладку ни на байт ---
        days = _posting_days(start, end, b["posts_per_week"])
        for kd in sorted(key_by_date):
            if start <= kd <= end and kd not in days:
                days.append(kd)
        return [
            {"date": day, "platforms": list(all_platforms),
             "formats": list(global_formats), "key": day in key_by_date,
             "per_platform": False}
            for day in sorted(set(days))
        ]

    # --- новый путь: своё количество на каждую площадку ---
    events = []
    for plat in all_platforms:
        vol = per_platform_volume.get(plat, b["posts_per_week"])
        fmts = per_platform_formats.get(plat) or global_formats
        for day in _posting_days(start, end, vol):
            events.append({"date": day, "platforms": [plat],
                           "formats": list(fmts), "key": False,
                           "per_platform": True})
    # ключевые даты — отдельный приоритетный пост на все площадки
    for kd in sorted(key_by_date):
        if start <= kd <= end:
            events.append({"date": kd, "platforms": list(all_platforms),
                           "formats": list(global_formats), "key": True,
                           "per_platform": True})
    # детерминированный порядок: по дате, ключевые даты раньше обычных, затем
    # по площадке — чтобы обход рубрик был стабилен и воспроизводим в тестах.
    events.sort(key=lambda e: (e["date"], 0 if e["key"] else 1,
                               ",".join(e["platforms"])))
    return events


def build_plan(brief: dict) -> dict:
    """Собирает контент-план из brief. Возвращает dict плана (не сохраняет)."""
    # Банк тем — заново на каждый прогон (BUG-2026-08-08): книга смыслов
    # клиента может обновиться между прогонами, приоритет всегда за ней,
    # заготовка-донор — только фолбэк на недостающие рубрики.
    bank, bank_meta = topics.load_topic_bank()
    b = _norm_brief(brief, bank)
    start = _parse_date(b["period"]["start"])
    end = _parse_date(b["period"]["end"])
    veto_hours = int(b["veto_hours"])
    rubrics = b["rubrics"]
    post_time = str(b["post_time"] or config.DEFAULT_POST_TIME)
    tz_name = b["post_timezone"]

    # ключевые даты → приоритетные слоты
    key_by_date = {}
    for kd in b["key_dates"]:
        try:
            key_by_date[_parse_date(kd.get("date"))] = kd.get("note", "")
        except (ValueError, TypeError):
            continue

    # кол-во на каждую площадку (или None → общий posts_per_week, как раньше)
    per_platform_volume = _resolve_platform_volume(b)
    per_platform_formats = _resolve_platform_formats(b)
    # фиксируем разрешённые значения в брифе ТОЛЬКО когда фича включена —
    # старый бриф без новых ключей остаётся байт-в-байт.
    if per_platform_volume is not None:
        b["per_platform_volume"] = per_platform_volume
    if per_platform_formats:
        b["per_platform_formats"] = per_platform_formats

    events = _build_events(b, start, end, key_by_date,
                           per_platform_volume, per_platform_formats)

    # рабочий банк идей: детерминированная база (llm_ideas без generate — сети
    # нет). Сюда позже можно подать generate=... для «умного» расширения пула.
    idea_pool = _build_idea_pool(rubrics, b, bank)

    plan = model.empty_plan()
    plan["brief"] = b
    # Откуда взята каждая рубрика — из книги смыслов клиента или заготовки
    # модуля (BUG-2026-08-08). Читает doctor.py, чтобы честно показать, чей
    # это на самом деле контент, а не тихо выдавать заготовку за клиентское.
    plan["topic_bank_meta"] = bank_meta

    used_topics = set()
    used_hooks = set()
    cursors = {}
    rubric_i = 0  # отдельный счётчик рубрик: ключевые даты его НЕ сдвигают,
    #               поэтому обычные слоты всегда крутят рубрики равномерно.
    for i, ev in enumerate(events):
        day = ev["date"]
        ev_formats = ev["formats"] or ["carousel"]
        fmt = ev_formats[i % len(ev_formats)]
        publish_at = "%sT%s:00" % (day.isoformat(), post_time)
        veto_opens = (datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%S")
                      - timedelta(hours=veto_hours))

        if ev["key"]:
            note = key_by_date.get(day, "")
            rubric = "Продажи"
            topic = ("АКЦИЯ: " + note, note,
                     "ключевая дата периода: " + note)
        else:
            rubric = rubrics[rubric_i % len(rubrics)]
            rubric_i += 1
            topic = _pick_topic(rubric, idea_pool, cursors,
                                used_topics, used_hooks)

        if ev["per_platform"]:
            plat_tag = (ev["platforms"][0] if len(ev["platforms"]) == 1
                        else "all")
            slot_id = "%s-%s-%s-%d" % (day.isoformat(), plat_tag, fmt, i + 1)
        else:
            slot_id = "%s-%s-%d" % (day.isoformat(), fmt, i + 1)

        slot = {
            "id": slot_id,
            "date": day.isoformat(),
            "time": post_time,
            "publish_at": publish_at,
            "format": fmt,
            "rubric": rubric,
            "topic": topic[0],
            "hook": topic[1],
            "meaning": topic[2],
            "platforms": list(ev["platforms"]),
            "status": model.PLANNED,
            "artifact": None,
            "generated_at": None,
            "veto_opens_at": veto_opens.replace(microsecond=0).isoformat(),
            # Время публикации СО ЗОНОЙ клиента (BOX-26): наивное publish_at
            # остаётся для арифметики гейта на машине клиента, а это поле —
            # однозначная запись момента, которую можно показать и сверить.
            "timezone": tz_name,
            "publish_at_tz": aware_iso(publish_at, tz_name),
            "history": [],
        }
        model.add_history(slot, "planned", "создан планировщиком")
        plan["slots"].append(slot)

    return plan
