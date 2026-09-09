# -*- coding: utf-8 -*-
"""Ручной прогон аналитики контента. Всё через argparse — никакого input()
(CONTRACT.md §3): значения приходят аргументами или из конфига/образца.

Подкоманды:
    questions              показать вопросы диалога настройки
    example-metrics        записать шаблон выгрузки метрик в папку analytics/
    ingest [--live]        снять метрики из источника и сложить в хранилище
    probe [--json]         сухой прогон живого сбора — что будет, БЕЗ сети
    status                 состояние живого сбора: ключ, журнал, свежесть (без сети)
    history [--post ID]    динамика метрик между замерами
    profile [--name …]     ЖИВЫЕ агрегаты профиля (тратит лимит 100 запросов/5 мин)
    list                   показать посты в хранилище с производными ставками
    recommend [--post ID]  прогнать движок рекомендаций (по всем или одному посту)
    feedback               свернуть советы в подсказки для планировщика (в файл)

Ничего не публикует. Живой сбор — через Upload-Post и только при наличии ключа;
без ключа, без сети и при ошибке API — честный откат (кэш → образец → «данных
нет» с причиной), конвейер не падает. Платный аккаунт вслепую не трогается.
Сеть используют ТОЛЬКО `ingest --live` и `profile`; остальные команды офлайновые.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import (cache, config, history, live, model, paths, providers,
               published, questions, recommender, upload_post)


def cmd_questions(a):
    print("=== Вопросы под аналитику контента (задаёт Claude Code, не модуль) ===")
    print(questions.render())
    return 0


def cmd_example_metrics(a):
    paths.ensure_dirs()
    posts = providers.load_sample()
    path = paths.data_dir() / "metrics.example.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"posts": posts}, fh, ensure_ascii=False, indent=2)
    print("Шаблон выгрузки метрик записан: %s" % path)
    print("Замени raw-блоки своими выгрузками Insights и укажи имя файла в "
          "настройке analytics_sample_file, затем: python -m analytics.cli ingest")
    return 0


def _collapse(notes):
    """Схлопывает одинаковые по СУТИ заметки: когда причина у всех постов одна
    (например «нет ключа»), печатаем её один раз со списком постов, а не пять
    одинаковых абзацев. Заметка выглядит как «  i <post_id>: <причина>»."""
    order, groups = [], {}
    for note in notes:
        head, sep, reason = note.partition(": ")
        if not sep:
            order.append(note)
            groups[note] = None
            continue
        mark, _, pid = head.strip().partition(" ")
        key = (mark, reason)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(pid)
    out = []
    for key in order:
        if groups[key] is None:
            out.append(key)
            continue
        mark, reason = key
        ids = groups[key]
        if len(ids) == 1:
            out.append("  %s %s: %s" % (mark, ids[0], reason))
        else:
            out.append("  %s %d постов (%s): %s"
                       % (mark, len(ids), ", ".join(ids), reason))
    return out


def _posts_to_ingest(a):
    """Откуда брать СПИСОК постов для снятия метрик.

    По умолчанию — журнал публикаций `.state/published_ids.jsonl` (его пишет
    autopost при реальной отправке): это настоящие вышедшие посты с request_id.
    Если журнала нет или явно просят `--sample` — встроенный образец/ручная
    выгрузка. Возвращает (список, откуда, пояснение)."""
    if not a.sample:
        cards = published.to_cards()
        if cards:
            return cards, "журнал публикаций", str(paths.published_log_path())
        if a.published:
            return [], "журнал публикаций", (
                "журнала %s нет или он пуст — реальных публикаций ещё не было"
                % paths.published_log_path())
    return providers.load_sample(), "образец", config.sample_file()


def cmd_ingest(a):
    src = config.metrics_source()
    want_live = a.live or config.live_source_requested()
    posts, origin, where = _posts_to_ingest(a)
    if not posts:
        print("Нечего снимать: %s (%s)." % (origin, where))
        print("Опубликуйте пост (autopost.py --send) либо положите свою выгрузку: "
              "python -m analytics.cli example-metrics")
        return 1

    # Карта «главное поле показов по площадке» — ЛОКАЛЬНАЯ фикстура: живого
    # эндпоинта platform-metrics в API нет, по сети за ней не ходим.
    field_map = providers.load_field_map()

    session = live.LiveSession() if want_live else None
    if session is not None:
        ok_live, why = session.available()
        if not ok_live:
            print("Живой сбор запрошен, но недоступен: %s." % why)
            print("Работаем на образце — платный аккаунт не тронут.")

    store = model.load_store()
    ok, skipped, notes = 0, 0, []
    fresh, from_cache, from_sample = 0, 0, 0
    for post in posts:
        res = providers.fetch_metrics(post, live=want_live, field_map=field_map,
                                      session=session)
        if not res.get("ok"):
            skipped += 1
            notes.append("  - %s: %s" % (post.get("post_id"), res.get("reason")))
            continue
        rec = model.new_record(post, res["metrics"], res.get("source", "sample"))
        errs = model.validate(rec)
        if errs:
            skipped += 1
            notes.append("  - %s: схема — %s" % (rec["post_id"], "; ".join(errs)))
            continue
        model.upsert(store, rec)
        history.append(rec, captured_at=res.get("captured_at"))
        ok += 1
        if res["source"] == "upload_post":
            fresh += 1
        elif res["source"] == "upload_post_cache":
            from_cache += 1
        else:
            from_sample += 1
        if res.get("note"):
            notes.append("  i %s: %s" % (rec["post_id"], res["note"]))
    model.save_store(store)

    print("Снято метрик: %d постов (пропущено %d). Список постов: %s (%s)."
          % (ok, skipped, origin, where))
    print("Откуда цифры: живьём %d, из кэша %d, образец %d. Настройка источника: %s%s."
          % (fresh, from_cache, from_sample, src,
             " (запрошен live)" if want_live else ""))
    if session is not None:
        for note in session.notes:
            notes.append("  i %s" % note)
    if notes:
        print("Заметки:")
        for line in _collapse(notes):
            print(line)
    print("Хранилище: %s" % paths.store_path())
    print("История замеров: %s (динамика: python -m analytics.cli history)"
          % paths.history_path())
    if want_live and not upload_post.has_key():
        print("Ключа UPLOAD_POST_API_KEY нет — живой Upload-Post недоступен, взят "
              "образец. Платный аккаунт не тронут (см. DESIGN §7).")
    return 0


def cmd_history(a):
    """Динамика: как метрики поста менялись между замерами."""
    summ = history.summary()
    if not summ["measurements"]:
        print("История замеров пуста. Она наполняется при каждом снятии метрик: "
              "python -m analytics.cli ingest")
        print("Файл истории: %s" % paths.history_path())
        return 0
    print("=== История замеров ===")
    print("Всего замеров: %d по %d постам. Первый: %s, последний: %s."
          % (summ["measurements"], summ["posts"], summ["first"], summ["last"]))
    if a.post:
        ids = [a.post]
    else:
        ids = sorted({r.get("post_id") for r in history.read()})
    print()
    print("  %-20s %-8s %10s %10s %9s %10s"
          % ("post_id", "замеров", "было", "стало", "прирост", "в сутки"))
    for pid in ids:
        d = history.delta(pid, field=a.field)
        if not d.get("ok"):
            print("  %-20s %s" % (pid, d.get("reason")))
            continue
        print("  %-20s %-8d %10s %10s %9s %10s"
              % (pid, d["measurements"], _fmt(d["first"]), _fmt(d["last"]),
                 _fmt(d["growth"]), _fmt(d["per_day"])))
    print()
    print("Метрика: %s. Другую — ключом --field (views/reach/likes/saves/…)."
          % (a.field or "базовая аудитория (охват→показы→просмотры)"))
    return 0


def cmd_status(a):
    """Состояние живого сбора: ключ, профиль, журнал публикаций, свежесть данных.
    НИЧЕГО не запрашивает по сети — только читает локальное состояние."""
    print("=== Состояние живого сбора Upload-Post ===")
    print("Ключ UPLOAD_POST_API_KEY: %s" % upload_post.key_shape())
    print("Профиль (UPLOAD_POST_USER / analytics_profile_username): %s"
          % (upload_post.read_user() or config.profile_username() or "не задан"))
    print("Источник метрик в конфиге: %s" % config.metrics_source())
    pub = published.summary()
    if pub["exists"]:
        print("Журнал публикаций: %d строк, %d публикаций, площадки: %s"
              % (pub["rows"], pub["requests"], ", ".join(pub["platforms"]) or "—"))
    else:
        print("Журнал публикаций (%s) ещё не создан — реальных отправок не было."
              % paths.published_log_path())
    stamp = cache.last_success()
    if stamp:
        print("Последнее УСПЕШНОЕ обновление живых данных: %s (%s ч назад)"
              % (stamp, cache.age_hours(stamp)))
    else:
        print("Живой сбор ещё ни разу не срабатывал — данных из Upload-Post нет.")
    summ = history.summary()
    print("История замеров: %d замеров по %d постам."
          % (summ["measurements"], summ["posts"]))
    print()
    print("Сеть при этой команде НЕ используется. Живой сбор: "
          "python -m analytics.cli ingest --live")
    return 0


def cmd_list(a):
    store = model.load_store()
    posts = store.get("posts", [])
    if not posts:
        print("Хранилище пусто. Сними метрики: python -m analytics.cli ingest")
        return 0
    print("=== Метрики опубликованных постов (%d) ===" % len(posts))
    _print_data_origin(posts)
    print("  %-16s %-9s %-8s  дсм%%  вовл%%  сохр%%  ctr%%   тема"
          % ("post_id", "площадка", "формат"))
    for p in posts:
        d = p.get("derived") or {}
        print("  %-16s %-9s %-8s  %-4s  %-5s  %-5s  %-5s  %s"
              % (p["post_id"], p["platform"], p["format"],
                 _fmt(d.get("retention_pct")), _fmt(d.get("engagement_rate_pct")),
                 _fmt(d.get("save_rate_pct")), _fmt(d.get("ctr_pct")),
                 p.get("topic", "")))
    return 0


def _fmt(v):
    return "—" if v is None else ("%g" % round(v, 1))


#: Источники записи, означающие «это НЕ ваши цифры»: встроенный демо-образец и
#: ручная выгрузка-шаблон. Хранятся в record["source"] (model.new_record).
_DEMO_SOURCES = ("sample",)


def _print_data_origin(posts):
    """Сказать вслух, ЧЬИ это цифры, прежде чем показывать выводы по ним.

    ЗАЧЕМ. Источник метрик по умолчанию — `sample`, встроенный демо-образец. При
    этом шапки команд говорили «Метрики опубликованных постов» и «Рекомендации по
    опубликованным постам», ни словом не оговариваясь, что посты не клиента.
    Человек читал разбор чужих демо-чисел как разбор своего аккаунта и принимал
    по нему решения. Строку печатаем ВСЕГДА — и когда цифры живые (тогда это
    подтверждение), и когда демонстрационные (тогда это предупреждение).
    """
    kinds = {}
    for p in posts or []:
        kinds[p.get("source") or "sample"] = kinds.get(p.get("source") or "sample", 0) + 1
    demo = sum(n for s, n in kinds.items() if s in _DEMO_SOURCES)
    total = sum(kinds.values())
    if not total:
        return
    if demo == total:
        print("⚠ ЭТО ДЕМО-ОБРАЗЕЦ, А НЕ ВАШИ ПОСТЫ: все %d записей взяты из "
              "встроенного образца (source=sample). Числа и выводы ниже — не про "
              "ваш аккаунт. Чтобы считать по своим постам: настройка "
              "analytics_metrics_source=upload_post + ключ Upload-Post, затем "
              "python -m analytics.cli ingest --live" % total)
    elif demo:
        print("⚠ ЧАСТЬ ЦИФР — ДЕМО-ОБРАЗЕЦ: %d записей из %d взяты из встроенного "
              "образца (source=sample), выводы по ним не про ваш аккаунт."
              % (demo, total))
    else:
        print("Цифры ваши: %d записей из живого сбора (%s)."
              % (total, ", ".join(sorted(kinds))))


def cmd_recommend(a):
    store = model.load_store()
    if not store.get("posts"):
        print("Хранилище пусто. Сними метрики: python -m analytics.cli ingest")
        return 1
    thr = config.thresholds()
    if a.post:
        rec = model.find_post(store, a.post)
        if rec is None:
            print("Нет поста %s в хранилище." % a.post)
            return 1
        blocks = [recommender.recommend_one(rec, store, thr)]
    else:
        blocks = recommender.recommend_store(store, thr)

    # сохраняем последний прогон в свою папку (замер, не вывод)
    paths.ensure_dirs()
    with paths.recs_path().open("w", encoding="utf-8") as fh:
        json.dump({"generated_at": model.now_iso(), "thresholds": thr,
                   "blocks": blocks}, fh, ensure_ascii=False, indent=2)

    if a.json:
        print(json.dumps(blocks, ensure_ascii=False, indent=2))
        return 0

    print("=== Рекомендации по опубликованным постам ===")
    _print_data_origin([model.find_post(store, b["post_id"]) or {} for b in blocks])
    # Из чего собран совет — не менее важно, чем сам совет. «Умный» шаг
    # (recommender.llm_advice) намеренно оставлен заглушкой, и человек имеет
    # право знать, что перед ним пороговые правила, а не мнение модели.
    print("Советы собраны движком правил «метрика→совет» по порогам ниже — "
          "без ИИ, без обращений в сеть и без трат на API.")
    print("Пороги: досмотр ≥%d%%, переходы ≥%.1f%%, сохранения ≥%.1f%%, "
          "вовлечённость ≥%.1f%%, охват не ниже медианы на %d%%."
          % (thr["retention_pct"], thr["ctr_pct"], thr["save_rate_pct"],
             thr["engagement_rate_pct"], thr["reach_drop_pct"]))
    for b in blocks:
        print()
        print("• %s [%s/%s] «%s»"
              % (b["post_id"], b["platform"], b["format"], b.get("topic", "")))
        for r in b["recommendations"]:
            mark = {"high": "!!", "med": "! ", "low": "· ", "good": "✓ "}.get(
                r["severity"], "  ")
            print("   %s %s" % (mark, r["advice"]))
    print()
    print("Прогон сохранён: %s" % paths.recs_path())
    print("Подсказки планировщику: python -m analytics.cli feedback")
    return 0


def cmd_feedback(a):
    store = model.load_store()
    if not store.get("posts"):
        print("Хранилище пусто. Сними метрики и прогони recommend.")
        return 1
    blocks = recommender.recommend_store(store, config.thresholds())
    fb = recommender.planner_feedback(blocks)
    paths.ensure_dirs()
    with paths.feedback_path().open("w", encoding="utf-8") as fh:
        json.dump(fb, fh, ensure_ascii=False, indent=2)
    print("=== Подсказки для планировщика (ПРЕДЛОЖЕНИЕ, planner не трогаем) ===")
    _print_data_origin(store.get("posts"))
    if fb["scale_up"]:
        print("Масштабировать (зашло):")
        for s in fb["scale_up"]:
            print("  + %s — %d удачных" % (s["rubric_format"], s["wins"]))
    if fb["fix_repeating"]:
        print("Повторяющиеся проблемы:")
        for s in fb["fix_repeating"]:
            print("  ! %s — %d раз (рубрики: %s)"
                  % (s["action"], s["count"], ", ".join(s["rubrics"]) or "—"))
    print("Файл-подсказка: %s" % paths.feedback_path())
    print("Как planner мог бы это учитывать — в DESIGN §6. Сам planner не вызван.")
    return 0


def cmd_probe(a):
    """Сухой прогон живого сбора БЕЗ ЕДИНОГО СЕТЕВОГО ВЫЗОВА.

    Показывает: какие посты пойдут в живой сбор, по какому ключу связи каждый из
    них будет искаться и сколько запросов к Upload-Post это породит. Ничего не
    тратит и ничего не публикует — это ответ на вопрос «что будет, если запустить
    ingest --live». Плюс печатает локальную карту главных полей показов."""
    print("=== Сухой прогон живого сбора (сети НЕТ, аккаунт не тратится) ===")
    print("Ключ Upload-Post: %s | профиль: %s | источник в конфиге: %s"
          % (upload_post.key_shape(),
             upload_post.read_user() or config.profile_username() or "не задан",
             config.metrics_source()))

    posts, origin, where = _posts_to_ingest(a)
    print("Список постов: %s (%s), всего %d." % (origin, where, len(posts)))
    plats, ready, blocked = set(), 0, []
    for p in posts:
        plat = str(p.get("platform") or "").lower()
        up = upload_post.PLATFORM_MAP.get(plat)
        if up is None:
            blocked.append("%s: площадка %r не покрыта аналитикой Upload-Post"
                           % (p.get("post_id"), plat))
            continue
        link = ("request_id=%s" % p["request_id"]) if p.get("request_id") else (
            "ссылка/нативный id" if (p.get("url") or p.get("platform_post_id"))
            else None)
        if not link:
            blocked.append("%s: нет ключа связи (ни request_id, ни ссылки) — "
                           "живьём не найдём, пойдёт образец" % p.get("post_id"))
            continue
        plats.add(up)
        ready += 1
    print("Годятся для живого сбора: %d, не годятся: %d." % (ready, len(blocked)))
    for b in blocked:
        print("  - " + b)
    if ready:
        print("Запросов к Upload-Post это дало бы: %d пачек снимков "
              "(post-analytics/cached, площадки: %s) плюс до %d уточнений статуса. "
              "Пачка кэша НЕ дёргает площадки и не тратит лимит 100 запросов/5 мин."
              % (len(plats), ", ".join(sorted(plats)) or "—", ready))

    fmap = providers.load_field_map()
    if fmap:
        print("Карта главных полей показов (локальная, %s):"
              % providers.FIELD_MAP_FIXTURE)
        for plat in sorted(fmap):
            print("  %-12s → %s" % (plat, fmap[plat]))
    if a.json:
        print(json.dumps({"posts": len(posts), "ready": ready,
                          "blocked": blocked, "field_map": fmap},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_profile(a):
    """ЖИВЫЕ агрегаты профиля: GET /api/analytics/{profile}?platforms=…

    Отдельной командой, а НЕ внутри ingest: этот эндпоинт дёргает сами площадки и
    попадает под лимит 100 запросов / 5 минут. Требует ключ; без ключа честно
    отказывается и в сеть не идёт."""
    name = a.name or config.profile_username() or upload_post.read_user()
    if not name:
        print("Не задан профиль. Укажите --name <профиль> либо настройку "
              "analytics_profile_username / переменную UPLOAD_POST_USER.")
        return 1
    if not upload_post.has_key():
        print("Нет ключа UPLOAD_POST_API_KEY — живые агрегаты профиля недоступны. "
              "В сеть не пошли, платный аккаунт не тронут.")
        return 1
    plats = a.platforms or "instagram"
    print("Запрашиваю живые агрегаты профиля %s (%s). Это ЖИВОЙ вызов Upload-Post: "
          "он дёргает площадки и расходует лимит 100 запросов / 5 минут."
          % (name, plats))
    key_name = "profile:%s:%s" % (name, plats)
    try:
        raw = upload_post.profile_analytics(name, platforms=plats)
    except upload_post.UploadPostError as ex:
        cached_raw, saved_at = cache.get(key_name)
        if cached_raw is None:
            print("Не получилось: %s" % ex)
            print("Данных нет — ни живьём, ни в кэше. Это не поломка модуля: "
                  "проверьте сеть и ключ, попробуйте позже.")
            return 1
        print("Живой вызов не удался (%s) — показываю последний успешный ответ "
              "из кэша от %s." % (ex, saved_at))
        raw = cached_raw
    else:
        cache.put(key_name, raw)
    print(json.dumps(raw, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Аналитика опубликованного контента + рекомендации.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("questions", help="показать вопросы диалога настройки")
    sub.add_parser("example-metrics", help="записать шаблон выгрузки метрик")

    def add_source_flags(parser):
        """Общие ключи «откуда брать список постов»."""
        parser.add_argument("--sample", action="store_true",
                            help="брать список постов из образца/ручной выгрузки, "
                                 "а не из журнала публикаций")
        parser.add_argument("--published", action="store_true",
                            help="только журнал публикаций .state/"
                                 "published_ids.jsonl (без отката на образец)")

    p_in = sub.add_parser("ingest", help="снять метрики в хранилище")
    p_in.add_argument("--live", action="store_true",
                      help="живой сбор через Upload-Post (нужен ключ; без ключа, "
                           "без сети и при ошибке API — честный откат)")
    add_source_flags(p_in)

    p_pr = sub.add_parser("probe", help="сухой прогон живого сбора (без сети)")
    p_pr.add_argument("--json", action="store_true", help="машинный вывод")
    add_source_flags(p_pr)

    p_prof = sub.add_parser("profile", help="живые агрегаты профиля (тратит лимит)")
    p_prof.add_argument("--name", help="имя профиля Upload-Post")
    p_prof.add_argument("--platforms", help="через запятую: instagram,tiktok,…")

    sub.add_parser("status", help="состояние живого сбора (без сети)")

    p_hi = sub.add_parser("history", help="динамика метрик между замерами")
    p_hi.add_argument("--post", help="id одного поста (иначе все)")
    p_hi.add_argument("--field", help="метрика: views/reach/likes/saves/… "
                                      "(по умолчанию базовая аудитория)")

    sub.add_parser("list", help="показать посты с производными ставками")

    p_rec = sub.add_parser("recommend", help="прогнать движок рекомендаций")
    p_rec.add_argument("--post", help="id одного поста (иначе все)")
    p_rec.add_argument("--json", action="store_true", help="машинный вывод")

    sub.add_parser("feedback", help="подсказки планировщику (в файл)")

    a = ap.parse_args(argv)
    handlers = {
        "questions": cmd_questions, "example-metrics": cmd_example_metrics,
        "ingest": cmd_ingest, "probe": cmd_probe, "list": cmd_list,
        "recommend": cmd_recommend, "feedback": cmd_feedback,
        "profile": cmd_profile, "status": cmd_status, "history": cmd_history,
    }
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
