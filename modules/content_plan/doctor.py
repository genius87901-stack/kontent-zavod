# -*- coding: utf-8 -*-
"""doctor модуля content_plan (контент-план + автономный исполнитель + вето).

Печатает ПО-РУССКИ человеческим языком, что готово и что поправить. Без
стектрейсов.

Коды выхода — СТЕПЕНЬ БЕДЫ (CONTRACT.md §2, BOX-51):
    0 — модуль полностью работоспособен: план собирается из книги смыслов
        клиента, черновики собираются по-настоящему, готовое уходит в выпуск.
    2 — УРЕЗАННЫЙ РЕЖИМ. В прототипе это ШТАТНОЕ состояние: жёсткий стоп
        публикации (gate.REAL_PUBLISH_IMPLEMENTED=False) и dry-run сборки
        черновиков (env CONTENT_PLAN_REAL_GEN не задан) означают, что план и
        расписание работают, а вот сами посты клиент собирает и публикует
        руками. Сюда же — недоступная витрина, банк тем на заготовках модуля,
        нехватка сырья, слоты сторис (движка сторис в заводе нет) и разговорный
        ролик, который не доходит до готового файла сам (нет весов расшифровки
        речи ЛИБО нет ключа модели на смысловую разбивку).
    1 — НЕ ГОТОВ: план невалиден или повреждён, стоп-темы просочились, сорван
        предохранитель публикации, ЛИБО сама проверка не смогла отработать.

Запуск (оба варианта): python -m content_plan.doctor  /  python modules/content_plan/doctor.py
"""
from __future__ import annotations

import sys

try:
    from . import config, executor, gate, model, paths, topics
except ImportError:  # запуск файлом напрямую
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from content_plan import config, executor, gate, model, paths, topics


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _autopost_path_intact() -> bool:
    """Ходит ли гейт на публикацию ШТАТНЫМ путём — через autopost.py --send.

    Ничего не публикует и не запускает: собирает команду для вымышленного слота
    и смотрит на её форму. Нужна только тогда, когда жёсткий стоп прототипа снят.
    """
    probe = {"id": "проверка-доктора", "format": "carousel",
             "platforms": ["instagram"], "publish_at": "2000-01-01T00:00:00"}
    try:
        cmd = gate.build_autopost_command(probe)
    except Exception:
        return False
    return "autopost.py" in cmd and cmd.strip().endswith("--send")


def check():
    """Проверка модуля. Возвращает три списка:

        problems — модуль НЕ готов (код 1): план невалиден/повреждён либо сорван
                   предохранитель. Красная тревога владельцу.
        limits   — УРЕЗАННЫЙ РЕЖИМ (код 2): план работает, но часть пути клиент
                   проходит руками. Обычная строка суточной сводки.
        lines    — наблюдения, на код возврата не влияют.
    """
    problems, limits, lines = [], [], []

    # План читаем заранее: от того, собран он или нет, зависит, показывать
    # реальные значения плана или умолчания для новых планов.
    plan = None
    try:
        plan = model.load_plan()
    except (ValueError, OSError) as ex:
        problems.append("Файл плана повреждён и не читается: %s. Расписание "
                        "выпуска прочитать нельзя — собери план заново." % ex)
        plan = None
    has_plan = bool(plan and plan.get("slots"))

    # 1. Параметры планировщика и окна-вето. Если план УЖЕ собран — берём их
    #    ИЗ plan.brief (реальные значения этого плана), а не встроенные
    #    умолчания: иначе doctor соврёт клиенту, напечатав, например, 12 ч
    #    окна-вето, когда у утверждённого плана оно 24. Если плана нет —
    #    показываем умолчания, но честно помечаем их как «для новых планов».
    if has_plan:
        b = plan.get("brief", {}) or {}
        hor = _as_int(b.get("horizon_days"), config.plan_horizon_days())
        ppw = _as_int(b.get("posts_per_week"), config.posts_per_week())
        veto = _as_int(b.get("veto_hours"), config.veto_hours())
        head = "Параметры плана"
    else:
        hor, ppw, veto = (config.plan_horizon_days(),
                          config.posts_per_week(), config.veto_hours())
        head = "Умолчания для новых планов"
    if hor <= 0 or ppw <= 0 or veto < 0:
        problems.append(
            "%s заданы некорректно "
            "(горизонт=%s, постов/нед=%s, окно-вето=%s ч). Проверь настройки — "
            "нужны положительные числа. Пока это так, план собрать нельзя."
            % (head, hor, ppw, veto))
    else:
        lines.append("OK  %s: горизонт %d дн, %d постов/нед (общий темп), "
                     "окно-вето %d ч." % (head, hor, ppw, veto))

    # 1a. Количество контента на каждую площадку (per-platform volume). Опция:
    #     нет ключа → общий posts_per_week (прежнее поведение). Показываем, если
    #     задано в брифе собранного плана или в умолчаниях конфига.
    ppv = None
    if has_plan:
        b = plan.get("brief", {}) or {}
        raw_ppv = b.get("per_platform_volume")
        ppv = raw_ppv if isinstance(raw_ppv, dict) else config.parse_platform_volume(raw_ppv)
    if ppv is None:
        ppv = config.default_per_platform_volume()
    if ppv:
        pairs = ", ".join("%s: %d/нед" % (p, n) for p, n in sorted(ppv.items()))
        lines.append("OK  Кол-во на площадку: %s. Планировщик раскладывает "
                     "свои слоты каждой площадке." % pairs)
    else:
        lines.append("i   Кол-во на площадку не задано — все площадки идут "
                     "общим темпом (%d постов/нед). Это прежнее поведение." % ppw)

    # 1b. Банк тем: чей это на самом деле контент — клиента или заготовки
    #     модуля (BUG-2026-08-08). Раньше банк был захардкожен под Валерию без
    #     возможности подключить книгу смыслов другого клиента вообще —
    #     doctor об этом молчал. Берём meta из УЖЕ собранного плана, если он
    #     есть (реальный источник этого плана), иначе — свежий прогон банка.
    bank_meta = (plan.get("topic_bank_meta") if has_plan else None) or \
        topics.load_topic_bank()[1]
    src = bank_meta.get("source") or {}
    client_rubrics = sorted(r for r, s in src.items() if s == "client")
    default_rubrics = sorted(r for r, s in src.items() if s == "default")
    if not bank_meta.get("client_bible_ok"):
        limits.append("Книга смыслов для контент-плана не читается — весь банк "
                      "тем собран на ЗАГОТОВКАХ МОДУЛЯ, а не на вашем контенте: "
                      "план получится общим, темы придётся переписывать руками. "
                      "Задай путь через настройку script_bible_file (тот же "
                      "файл, что и у сценариста).")
    elif client_rubrics:
        lines.append("OK  Банк тем: рубрики из КНИГИ СМЫСЛОВ КЛИЕНТА: %s."
                     % ", ".join(client_rubrics))
        if default_rubrics:
            lines.append("i   Рубрики на заготовке модуля (нет своих тем в "
                         "книге смыслов): %s." % ", ".join(default_rubrics))
    else:
        limits.append("В книге смыслов не нашлось ни одной рубрики с темами — "
                      "весь банк тем на заготовках модуля, не на вашем "
                      "контенте: план получится общим. Допишите в книгу смыслов "
                      "темы по своим рубрикам.")
    for w in bank_meta.get("warnings") or []:
        lines.append("⚠️  %s" % w)

    # 2. Предохранитель публикации — ГЛАВНОЕ. Он обязан быть на месте.
    #    Но правда в том, что при включённом стопе модуль НЕ делает главного,
    #    что ждёт от него клиент: сам ничего не публикует. Раньше доктор писал
    #    об этом «OK» и уходил с кодом 0 — владелец видел зелёный модуль,
    #    который не публикует. Теперь это честный урезанный режим (код 2).
    if gate.REAL_PUBLISH_IMPLEMENTED:
        # Стоп снят — значит публикация возможна. Единственное, что тут обязан
        # проверить доктор: гейт по-прежнему отправляет ШТАТНЫМ путём (собирает
        # команду autopost.py с его собственным предохранителем --send), а не
        # завёл свой постинг в обход. Если штатного пути нет — это код 1,
        # красная тревога: наружу может уйти неконтролируемое.
        if _autopost_path_intact():
            lines.append("OK  Публикация включена и идёт ШТАТНЫМ путём: гейт "
                         "собирает ту же команду autopost.py --send, что вы "
                         "ввели бы руками. Свой обходной постинг не заведён.")
        else:
            problems.append(
                "ВНИМАНИЕ: снят жёсткий стоп реальной публикации "
                "(REAL_PUBLISH_IMPLEMENTED=True), а штатный путь отправки через "
                "autopost --send не подтверждается. Так посты могут уйти в обход "
                "предохранителя — немедленно останови расписание и покажи это "
                "человеку.")
    else:
        lines.append("OK  Предохранитель на месте: без вашего слова наружу не "
                     "уйдёт ничего. Гейт показывает и логирует.")
        limits.append("Автопубликация по плану ВЫКЛЮЧЕНА жёстким стопом "
                      "прототипа: план, расписание и окно-вето работают, но "
                      "выложить готовый пост на площадку система сама не может "
                      "— это делаете вы (или отдельный модуль публикации). "
                      "Снятие стопа — отдельная задача разработки, вам чинить "
                      "нечего.")
    if config.live_publish_enabled():
        lines.append("i   Конфиг content_plan_live_publish=true, но жёсткий стоп "
                     "прототипа всё равно ничего не отправит.")

    # 2a. Режим одобрения (окно-вето / авто-одобрение). Только логика подтверждения
    #     слотов — НЕ публикация: жёсткий стоп выше держит слоты в любом режиме.
    # Режим берём из брифа собранного плана (в нём ответ клиента на интервью),
    # и только если его там нет — из конфига. Иначе доктор соврёт: напечатает
    # daily, когда у утверждённого плана стоит week.
    mode = gate.plan_mode(plan) if has_plan else config.publish_approval_mode()
    horizon = config.approval_horizon_days(mode)
    # «Частота участия» — тот же publish_approval_mode, сформулированный как то,
    # как часто человек участвует/подтверждает выход контента.
    mode_human = config.participation_label(mode)
    raw_mode = config.config_value("publish_approval_mode")
    plan_mode = has_plan and (plan.get("brief") or {}).get(
        "publish_approval_mode") == mode
    if plan_mode:
        lines.append("OK  Частота участия: %s — %s (ответ клиента из брифа "
                     "плана)." % (mode, mode_human))
    elif raw_mode is None:
        lines.append("OK  Частота участия: %s — %s. Значение по умолчанию "
                     "(ключ publish_approval_mode не задан)." % (mode, mode_human))
    elif isinstance(raw_mode, str) and raw_mode.strip().lower() == mode:
        lines.append("OK  Частота участия: %s — %s." % (mode, mode_human))
    else:
        limits.append("Частота участия publish_approval_mode=%r не распознана — "
                      "взят самый осторожный режим daily (%s). Это значит, что "
                      "система будет спрашивать вас по каждому посту, даже если "
                      "вы просили реже. Поправьте значение — и вопросов станет "
                      "меньше." % (raw_mode, mode_human))
    if horizon > 0:
        lines.append("i   Даже при авто-одобрении (%s, горизонт %d дн) публикация "
                     "не идёт: жёсткий стоп держит слоты, наружу ничего не уходит."
                     % (mode, horizon))
    else:
        lines.append("i   Режим daily держит каждый слот до явного одобрения; "
                     "и всё равно поверх стоит жёсткий стоп публикации.")

    # 2b. Режим сборки черновиков (генерация ≠ публикация). Dry-run — это тоже
    #     урезанный режим: в очередь ложатся ОПИСАНИЯ будущих постов, а не сами
    #     посты. Клиент, читая «OK», думал, что контент готовится.
    if executor.real_gen_enabled():
        lines.append("OK  Сборка черновиков: РЕАЛЬНАЯ (env %s задан) — движки "
                     "собирают настоящие карусели и reels в очередь. Публикацию "
                     "это не включает." % executor.REAL_GEN_ENV)
    else:
        lines.append("i   Сборка черновиков: dry-run (движки не зовутся, API не "
                     "тратятся). Реальная — под env %s=1." % executor.REAL_GEN_ENV)
        limits.append("Черновики по плану СОБИРАЮТСЯ ВХОЛОСТУЮ (dry-run): в "
                      "очередь ложатся описания будущих постов, а не готовые "
                      "карусели и ролики — снимать и монтировать пока придётся "
                      "вам. Включается переменной окружения %s=1."
                      % executor.REAL_GEN_ENV)

    # 3. Состояние плана (если он уже собран). План уже прочитан в начале
    #    check() — второй раз не читаем: повторное чтение дублировало клиенту
    #    сообщение о повреждённом файле.
    if plan is not None:
        if not plan.get("slots"):
            lines.append("i   Плана ещё нет — это нормально. Собери его: "
                         "cli plan --brief <файл>.")
        else:
            st = model.counts(plan)
            appr = "утверждён" if plan.get("approved") else "НЕ утверждён"
            lines.append("OK  План на месте: %d слотов, %s. Статусы: %s."
                         % (len(plan["slots"]), appr, st))
            if not plan.get("approved"):
                lines.append("i   Пока план не утверждён, автономная генерация "
                             "не идёт — это защита, а не поломка.")

            # 4. План валиден: даты читаются, id уникальны, окно-вето до выхода,
            #    стоп-темы не просочились в темы слотов.
            _check_plan_valid(plan, problems, lines)

            # 5. Сырья хватает: чем движки будут собирать черновики.
            _check_material(plan, limits, lines)

            # 6. ГЛАВНОЕ: ничего не опубликовано без утверждения.
            _check_nothing_published_unapproved(plan, problems, lines)

            # 7. Витрина в личном телеграм-канале клиента (BOX-26).
            _check_showcase(plan, limits, lines)

    return problems, limits, lines


# --- 7. витрина -------------------------------------------------------------

def _check_showcase(plan, limits, lines):
    """Готовность витрины и состояние просмотра. Недоступная витрина — урезанный
    режим (код 2), а НЕ ошибка: клиент может работать офлайн и утверждать по
    таблице. Секретов не печатаем — только факт наличия."""
    from . import showcase
    ready, why = showcase.provider_status()
    if showcase.stub_enabled():
        # provider_status() при заглушке отвечает «готова» — это правда про вызов
        # провайдера и ложь про клиента: в Telegram не уходит ничего. Печатать
        # здесь «Витрина настроена» значило бы соврать в самой читаемой строке.
        limits.append("Витрина работает ЗАГЛУШКОЙ (переменная окружения %s): "
                      "сообщения пишутся файлами в %s, в Telegram НЕ уходит "
                      "ничего и клиент их не видит. Это режим отладки — на "
                      "рабочей коробке переменной быть не должно."
                      % (showcase.STUB_ENV, showcase.stub_dir()))
    elif ready:
        lines.append("OK  Витрина настроена: канал задан, токен бота есть "
                     "(значение не показываем). Выкладка — командой "
                     "`cli showcase --send`.")
    else:
        limits.append("Витрина в телеграме недоступна: %s Смотреть готовый "
                      "контент с телефона и одобрять его одним нажатием вы пока "
                      "не сможете — придётся открывать файлы плана. План, "
                      "черновики и утверждение работают и без неё." % why)

    b = (plan.get("brief") or {})
    tz = b.get("post_timezone") or config.default_timezone()
    lines.append("OK  Ежедневная выдача: %s (%s, UTC%s) — время хранится с "
                 "зоной, не «наивное»."
                 % (b.get("post_time") or config.default_post_time(),
                    tz, config.timezone_offset(tz)))

    revising = showcase.pending_revisions(plan)
    if revising:
        lines.append("i   Правки от клиента ждут переделки: %d слот(ов) — %s. "
                     "Такие слоты не одобрятся ни вручную, ни авто."
                     % (len(revising),
                        ", ".join(str(s.get("id")) for s in revising[:3])))

    state = showcase.package_readiness(plan)
    if state["ready"]:
        lines.append("OK  Просмотрено клиентом и ждёт одобрения пакета: %d шт."
                     % len(state["ready"]))


# --- 4. валидность плана ---------------------------------------------------

# Длина «основы» слова для сверки со стоп-темой. Русский склоняется: клиент
# запретил «обещания гарантированного дохода», а в теме стоит «Гарантированный
# доход» — по целым словам это не поймать, по первым 5 буквам («гаран», «доход»)
# поймать можно. Меньше 5 — начнутся ложные срабатывания.
_STEM = 5


def _stems(phrase: str):
    """Основы значимых слов фразы (короткие слова — предлоги и связки — мимо)."""
    return [w[:_STEM] for w in phrase.lower().split() if len(w) > 4]


def _forbidden_hit(forbidden: str, text: str) -> bool:
    """Похоже ли, что стоп-тема просочилась в текст слота.

    Одно значимое слово в стоп-теме («политика») → достаточно его. Несколько →
    требуем минимум два совпадения И не меньше половины: иначе рубрика «Кейсы»
    ловила бы стоп-тему «чужие кейсы без разрешения» на каждом кейсовом слоте."""
    stems = _stems(forbidden)
    if not stems:
        return False
    matched = sum(1 for st in stems if st in text)
    if len(stems) == 1:
        return matched == 1
    need = max(2, (len(stems) + 1) // 2)
    return matched >= need

def _check_plan_valid(plan, problems, lines):
    from datetime import datetime as _dt
    slots = plan.get("slots") or []
    bad_dates, dup, bad_veto = [], [], []
    seen = set()
    for s in slots:
        sid = s.get("id")
        if sid in seen:
            dup.append(sid)
        seen.add(sid)
        try:
            pub = _dt.strptime(s["publish_at"], "%Y-%m-%dT%H:%M:%S")
        except (KeyError, TypeError, ValueError):
            bad_dates.append(sid)
            continue
        try:
            opens = _dt.strptime(s["veto_opens_at"], "%Y-%m-%dT%H:%M:%S")
        except (KeyError, TypeError, ValueError):
            bad_veto.append(sid)
            continue
        if opens >= pub:
            bad_veto.append(sid)

    if bad_dates:
        problems.append("У %d слотов не читается время публикации (%s…). "
                        "Собери план заново."
                        % (len(bad_dates), ", ".join(map(str, bad_dates[:3]))))
    if dup:
        problems.append("Повторяются id слотов: %s. Собери план заново."
                        % ", ".join(map(str, sorted(set(dup))[:3])))
    if bad_veto:
        problems.append("У %d слотов окно-вето открывается не раньше выхода "
                        "(%s…) — клиент не успеет снять пост. Проверь veto_hours."
                        % (len(bad_veto), ", ".join(map(str, bad_veto[:3]))))
    if not (bad_dates or dup or bad_veto):
        lines.append("OK  План валиден: даты читаются, id уникальны, у каждого "
                     "слота окно-вето открывается раньше выхода.")

    # Стоп-темы: клиент назвал, о чём не пишем — проверяем темы и крючки.
    forbidden = [f for f in (plan.get("brief", {}).get("forbidden") or [])
                 if isinstance(f, str) and f.strip()]
    hits = []
    for s in slots:
        text = " ".join(str(s.get(k) or "")
                        for k in ("topic", "hook", "meaning", "rubric")).lower()
        for f in forbidden:
            if _forbidden_hit(f, text):
                hits.append((s.get("id"), f))
    if hits:
        problems.append("Стоп-темы просочились в план: %s. Убери или замени "
                        "слоты." % "; ".join("%s → «%s»" % h for h in hits[:3]))
    elif forbidden:
        lines.append("OK  Стоп-темы (%d шт) в план не просочились."
                     % len(forbidden))


# --- 5. хватает ли сырья ----------------------------------------------------

def _check_material(plan, limits, lines):
    """Сырьё берётся с диска клиента. Проверяем БЕЗ запуска движков и без
    единого платного вызова: только спрашиваем мосты, нашли бы они вход.

    Нехватка сырья — урезанный режим (код 2): слоты в плане останутся, но
    соберутся пустыми, с пометкой «нужен вход». Клиенту это чинится одним
    действием — закинуть фото/видео, — и бросать всё ради этого не надо."""
    slots = plan.get("slots") or []
    need = {}
    for s in slots:
        if s.get("status") in (model.PUBLISHED, model.VETOED):
            continue
        need[s.get("format")] = need.get(s.get("format"), 0) + 1

    if need.get("carousel"):
        try:
            from . import carousel_bridge
        except ImportError:
            from content_plan import carousel_bridge
        probe = next(s for s in slots if s.get("format") == "carousel")
        try:
            photos = carousel_bridge.resolve_photos(probe)
        except Exception as ex:            # мост не должен ронять доктора
            photos, ex_note = [], str(ex)
        else:
            ex_note = None
        if photos:
            lines.append("OK  Сырьё для каруселей: найдено %d кадров — на %d "
                         "слотов хватит." % (len(photos), need["carousel"]))
        else:
            limits.append("Нет фото для каруселей (%d слотов в плане)%s — эти "
                          "посты соберутся пустыми, с пометкой «нужен вход: "
                          "фото», и в срок ничего не выйдет. Закиньте кадры на "
                          "Google Диск или укажите каталог фото в настройке "
                          "content_plan_photos_dir."
                          % (need["carousel"], ": %s" % ex_note if ex_note else ""))

    if need.get("reels"):
        try:
            from . import reels_bridge
        except ImportError:
            from content_plan import reels_bridge
        probe = next(s for s in slots if s.get("format") == "reels")
        try:
            video = reels_bridge.resolve_video(probe)
        except Exception:
            video = None
        if video:
            lines.append("OK  Сырьё для reels: исходное говорящее видео "
                         "найдено (%d слотов)." % need["reels"])
        else:
            limits.append("Нет исходного говорящего видео для reels (%d слотов "
                          "в плане) — эти ролики соберутся пустыми, с пометкой "
                          "«нужен вход: video». Снимите и положите исходник на "
                          "Google Диск." % need["reels"])
        _check_reels_autonomy(need["reels"], limits, lines)

    if need.get("stories"):
        # 28.08.2026: сторис СОБИРАЮТСЯ (executor → stories_bridge →
        # story_publisher.story_builder). Раньше здесь стояла жалоба «движка
        # сборки сторис в заводе НЕТ» — неправда, движок лежал в соседнем
        # модуле, не было только шва. Проверяем ровно то, что может помешать
        # теперь: наличие самого модуля в этой копии.
        _check_stories_engine(need["stories"], limits)


def _check_stories_engine(count: int, limits) -> None:
    """Соберутся ли сторис САМИ. Один вопрос — один честный ответ.

    Движок сторис живёт в СОСЕДНЕМ модуле (modules/story_publisher). В коробку
    он едет вместе с content_plan, но копия могла быть собрана раньше, чем он
    появился, — тогда слоты сторис останутся строчками плана. Спрашиваем диск,
    а не свою память: путь до модуля, а не «мы вроде бы его кладём».

    Фото НЕ проверяем намеренно: сторис — единственный формат, которому входное
    фото не обязательно (нет материала → синтетический тёмный фон, см.
    stories_bridge). Требовать его значило бы придумать ограничение, которого у
    движка нет, и напугать клиента пустой тревогой.
    """
    engine = (config.install_root() / "modules" / "story_publisher"
              / "story_builder.py")
    if not engine.is_file():
        limits.append("Сторис в плане: %d слотов, а модуля сборки сторис в этой "
                      "копии завода нет (modules/story_publisher). Эти слоты "
                      "останутся строчками плана. Лечится обновлением завода." %
                      count)


# --- 5a. соберётся ли разговорный Reels САМ ---------------------------------

#: Кэш весов распознавания речи. Каталог задаёт движок (talk_transcribe._CACHE =
#: <корень>/.cache_models), имя модели внутри — раскладка HuggingFace. Читаем
#: диск и только диск: доктору нельзя ни в сеть, ни в тяжёлые импорты torch.
_MODELS_CACHE = ".cache_models"
_WHISPER_GLOB = "models--Systran--faster-whisper-*"

#: Ключ модели, которым делается смысловая разбивка речи (стадия talk_segment).
#: Спрашивается манифестом engine-media, required:false. Значение не печатаем.
_SEGMENT_KEY = "ANTHROPIC_API_KEY"


def whisper_weights_present() -> bool:
    """Лежат ли на диске веса расшифровки речи. Ни сети, ни импортов."""
    cache = config.install_root() / _MODELS_CACHE
    try:
        return cache.is_dir() and any(cache.rglob(_WHISPER_GLOB))
    except OSError:
        return False


def _check_reels_autonomy(count: int, limits, lines) -> None:
    """ОДИН честный ответ на один вопрос: соберётся ли разговорный ролик САМ.

    Раньше доктор про это молчал вовсе: писал «сырьё для reels найдено» — и
    клиент читал это как «ролики будут». А цепочка после видео состоит ещё из
    двух звеньев, каждое из которых само по себе останавливает автосборку:

      1. РАСШИФРОВКА РЕЧИ. Нет весов whisper в .cache_models — нет транскрипта,
         значит нет и блоков: упирается раньше всего остального. Это звено выше
         ключа модели по порядку, поэтому и сообщаем про него первым.
      2. СМЫСЛОВАЯ РАЗБИВКА. Нет ключа модели — блоки делает человек в живой
         сессии Claude. Это штатное и БЕСПЛАТНОЕ состояние коробки, не поломка.

    Оба случая — урезанный режим (код 2, CONTRACT.md §2): план, расписание и
    вето живут, а вот ролик до готового файла сам не доходит. Обе проверки
    смотрят только на диск и на факт наличия ключа — ни одного обращения наружу
    и ни одной копейки."""
    weights = whisper_weights_present()
    key = config.has_secret(_SEGMENT_KEY)

    if not weights:
        limits.append(
            "Разговорные ролики (%d слотов) САМИ НЕ СОБЕРУТСЯ: не скачаны веса "
            "распознавания речи — расшифровать, что вы говорите в кадре, нечем. "
            "Без расшифровки нет смысловых кусочков, без них нет перебивок и "
            "монтажа, поэтому это упирается раньше всего остального. Лечится "
            "одним шагом установки там, где есть интернет: python "
            "install/install_models.py (около 2.5–3 ГБ, один раз, бесплатно)."
            % count)
    elif not key:
        limits.append(
            "Разговорные ролики (%d слотов) собираются С УЧАСТИЕМ ЧЕЛОВЕКА: речь "
            "расшифровывается сама, а разложить её на смысловые кусочки без "
            "ключа помощника Claude завод не может — этот шаг делает живая "
            "сессия (talk_segment.py --print-prompt → --from-answer). Хотите "
            "полностью сами — заполните ключ %s при установке (платная услуга, "
            "счёт ваш). Ничего не тратится, пока поле пустое." % (count, _SEGMENT_KEY))
    else:
        lines.append(
            "OK  Разговорные ролики (%d слотов) собираются САМИ: веса "
            "расшифровки речи на месте, ключ помощника для смысловой разбивки "
            "заведён (значение не показываем). Учтите: каждая разбивка — платное "
            "обращение к модели по вашему ключу." % count)

    if not executor.real_gen_enabled():
        lines.append("i   Пока сборка черновиков в режиме dry-run (%s не задан), "
                     "ролик не собирается ни сам, ни с ключом — в очередь ложится "
                     "описание." % executor.REAL_GEN_ENV)


# --- 6. предохранитель: ничего не ушло без утверждения ----------------------

def _check_nothing_published_unapproved(plan, problems, lines):
    published = [s for s in (plan.get("slots") or [])
                 if s.get("status") == model.PUBLISHED]
    if not published:
        lines.append("OK  Ничего не опубликовано: все %d слотов в статусах до "
                     "публикации. Наружу не ушло ничего."
                     % len(plan.get("slots") or []))
        return

    if not gate.REAL_PUBLISH_IMPLEMENTED:
        problems.append(
            "В плане есть опубликованные слоты (%d), хотя жёсткий стоп "
            "публикации включён. Такого быть не может — файл плана правили "
            "руками. Проверь: %s"
            % (len(published), ", ".join(str(s.get("id")) for s in published[:3])))
        return

    unapproved = [s for s in published
                  if not (plan.get("approved") and s.get("approved_at"))]
    if unapproved:
        problems.append(
            "НАРУШЕН ГЛАВНЫЙ ЗАКОН: опубликованы слоты без утверждения (%s). "
            "Немедленно останови расписание и покажи это человеку."
            % ", ".join(str(s.get("id")) for s in unapproved[:3]))
    else:
        lines.append("OK  Все опубликованные слоты (%d) прошли утверждение "
                     "плана и утверждение выпуска." % len(published))


def main():
    try:
        problems, limits, lines = check()
    except Exception as ex:  # никаких стектрейсов клиенту
        print("Проверка модуля контент-плана не смогла отработать: %s" % ex)
        print("Это сбой самой проверки. Покажите строку выше человеку.")
        return 1

    print("=== Проверка контент-плана (модуль content_plan) ===")
    for line in lines:
        print(" ", line)
    if problems:
        print()
        print("Не работает — это чинить:")
        for p in problems:
            print("  - " + p)
        if limits:
            print()
            print("Кроме того, часть возможностей недоступна:")
            for lim in limits:
                print("  - " + lim)
        return 1
    if limits:
        print()
        print("Модуль работает, но в урезанном режиме — вот что делается не само:")
        for lim in limits:
            print("  - " + lim)
        print()
        print("Это не поломка и не срочно: план, расписание, окно-вето и "
              "утверждение работают уже сейчас — наружу без вашего слова ничего "
              "не уходит.")
        return 2
    print()
    print("Модуль готов. Автономная генерация пойдёт только после утверждения "
          "плана, публикация — через окно-вето и штатный autopost.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
