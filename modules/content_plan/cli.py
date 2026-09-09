# -*- coding: utf-8 -*-
"""Операции человека над контент-планом. Всё через argparse-подкоманды —
никакого input() (CONTRACT.md §3): значения приходят аргументами или из файлов,
диалог вопросов ведёт Claude Code по questions.py.

Подкоманды:
    questions              показать интервью планировщика (варианты + умолчания)
    interview --set K=V    записать ответы в brief.json (переиспользует прошлый)
    example-brief          записать brief.example.json (шаблон под ответы)
    plan --brief FILE      собрать план из brief.json (не публикует)
    status                 показать сводку плана
    table [--week|--month] человекочитаемая таблица плана (+ --md FILE)
    board                  доска окна-вето: что скоро уйдёт и что можно снять
    approve --who "..."    УТВЕРДИТЬ план — слово человека на период
    revoke                 снять утверждение
    approve-slot --slot ID утвердить КОНКРЕТНЫЙ выпуск (режим daily)
    veto --slot ID         снять слот (не уйдёт)
    reslot --slot ID       вернуть слот в план (перегенерится)

Витрина в личном телеграм-канале клиента и правки словами (BOX-26):
    showcase [--send]      выложить готовые единицы в канал-витрину
                           (без --send — пробный прогон, ничего не уходит)
    showcase-status        что в витрине, что ждёт просмотра, где правки
    revise --slot ID --text "..."   записать правку клиента (слот → «есть правки»)
    revise-done --slot ID  правка внесена (или --regenerate — пересобрать)
    approve-package        ОДОБРИТЬ ВЕСЬ ПАКЕТ целиком (слово человека)
    delivery [--apply]     расписание ежедневной выдачи по одной единице в день
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

from . import model, paths, planner, questions


def _load_brief(path_str: str) -> dict:
    from pathlib import Path
    p = Path(path_str)
    if not p.is_absolute():
        from .config import install_root
        # Сначала как путь от корня установки, потом — как имя файла в папке
        # данных модуля (чтобы работало и `--brief brief.json`).
        candidate = install_root() / p
        p = candidate if candidate.exists() else paths.data_dir() / p.name
    with p.open(encoding="utf-8-sig") as fh:
        return json.load(fh)


def cmd_questions(a):
    print("=== Интервью планировщика (задаёт Claude Code / мастер, не модуль) ===")
    print(questions.render())
    print()
    print("Ответы записываются так (что не назвали — берётся умолчание):")
    print("  python -m content_plan.cli interview --set horizon_days=7 "
          "--set formats=carousel,reels")
    return 0


# --- интервью: разбор ответов -----------------------------------------------

def _parse_answer(key: str, raw: str):
    """Строку `--set key=value` приводим к типу вопроса. Тип берём из
    questions.py, чтобы формат ответа объявлялся в одном месте."""
    q = questions.by_key(key)
    if q is None:
        raise ValueError("нет вопроса с ключом %r (см. cli questions)" % key)
    kind = q.get("type")
    raw = (raw or "").strip()
    if kind == "int":
        return int(raw)
    if kind == "list":
        return [x.strip() for x in raw.split(",") if x.strip()]
    if kind == "map":
        # "instagram: 5, telegram: 7"  ИЛИ  "instagram: reels, carousel; telegram: carousel"
        out = {}
        for part in raw.replace(";", "\n").split("\n"):
            for chunk in ([part] if ":" in part else []):
                k, _, v = chunk.partition(":")
                vals = [x.strip() for x in v.split(",") if x.strip()]
                if not k.strip() or not vals:
                    continue
                out[k.strip().lower()] = (int(vals[0])
                                          if vals[0].isdigit() and len(vals) == 1
                                          else vals)
        return out or None
    if kind == "date_notes":
        out = []
        for part in raw.split(";"):
            d, _, note = part.partition(":")
            if d.strip():
                out.append({"date": d.strip(), "note": note.strip()})
        return out
    return raw


def cmd_interview(a):
    """Складывает ответы клиента в brief.json. Переиспользование: прошлый
    brief.json подкладывается базой, поверх ложатся новые ответы — клиенту
    достаточно назвать только то, что он меняет."""
    paths.ensure_dirs()
    target = paths.brief_path()
    base = {}
    if target.exists() and not a.fresh:
        with target.open(encoding="utf-8-sig") as fh:
            base = json.load(fh)

    answers, bad = {}, []
    for item in (a.set or []):
        key, _, raw = item.partition("=")
        try:
            answers[key.strip()] = _parse_answer(key.strip(), raw)
        except ValueError as ex:
            bad.append("%s: %s" % (item, ex))
    if bad:
        for line in bad:
            print("Не понял ответ — " + line)
        return 1

    brief, warns = questions.merge_brief(answers, base)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(brief, fh, ensure_ascii=False, indent=2)

    print("Ответы сохранены: %s" % target)
    for w in warns:
        print("  ! " + w)
    print()
    print("Что получилось:")
    for key in questions.keys():
        if key in brief:
            val = brief[key]
            shown = ", ".join(str(x) for x in val) if isinstance(val, list) else val
            print("  %-22s %s" % (key, shown))
    print()
    print("Собрать план: python -m content_plan.cli plan --brief %s"
          % target.name)
    return 0


def cmd_example_brief(a):
    paths.ensure_dirs()
    # Базу берём из умолчаний интервью — чтобы шаблон не разъезжался с
    # questions.py, когда там появляется новый вопрос. Сверху — примерные
    # значения необязательных полей, чтобы было видно их формат.
    sample, _warns = questions.merge_brief({
        "period": {"start": "2026-08-01", "end": "2026-08-31"},
        "per_platform_volume": {"instagram": 5, "telegram": 7},
        "per_platform_formats": {"instagram": ["reels", "carousel"],
                                 "telegram": ["carousel"]},
        "key_dates": [{"date": "2026-08-15", "note": "старт набора на разбор"}],
    })
    path = paths.brief_example_path()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(sample, fh, ensure_ascii=False, indent=2)
    print("Шаблон брифа записан: %s" % path)
    print("Заполни его ответами клиента и собери план: "
          "python -m content_plan.cli plan --brief content_plan/brief.example.json")
    return 0


def cmd_plan(a):
    brief = _load_brief(a.brief)
    plan = planner.build_plan(brief)
    model.save_plan(plan)
    print("План собран: %d слотов, %s — %s"
          % (len(plan["slots"]), plan["brief"]["period"]["start"],
             plan["brief"]["period"]["end"]))
    print("Файл: %s" % paths.plan_path())
    print("НЕ утверждён. Пока план не утверждён — исполнитель ничего не генерит.")
    print("Посмотреть: python -m content_plan.cli status")
    print("Утвердить:  python -m content_plan.cli approve --who \"Валерия\"")
    return 0


def cmd_status(a):
    plan = model.load_plan()
    if not plan.get("slots"):
        print("Плана ещё нет. Собери: python -m content_plan.cli plan --brief <файл>")
        return 0
    b = plan.get("brief", {})
    period = b.get("period", {})
    print("=== Контент-план ===")
    print("Период     : %s — %s" % (period.get("start"), period.get("end")))
    print("Площадки   : %s" % ", ".join(b.get("platforms", [])))
    print("Утверждён  : %s" % ("ДА (%s, %s)" % (plan.get("approved_by"),
          plan.get("approved_at")) if plan.get("approved") else "нет"))
    print("Окно-вето  : %s ч" % b.get("veto_hours"))
    from .config import timezone_offset
    tz = b.get("post_timezone") or "—"
    print("Выдача     : ежедневно в %s (%s, UTC%s)"
          % (b.get("post_time"), tz, timezone_offset(tz)))
    cnt = model.counts(plan)
    print("Статусы    : %s" % (", ".join(
        "%s: %d" % (model.label(k), v) for k, v in sorted(cnt.items())) or "—"))
    print()
    for s in plan["slots"]:
        print("  %-34s %s  %-9s %-16s %s"
              % (s["id"], s["publish_at"], s["format"],
                 model.label(s.get("status")), s["topic"]))
    return 0


# --- человекочитаемая таблица плана (консоль + markdown) --------------------

_RU_WEEKDAY = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _slot_day(slot: dict):
    try:
        return datetime.strptime(slot["publish_at"], "%Y-%m-%dT%H:%M:%S").date()
    except (KeyError, ValueError):
        return None


def _window(plan: dict, span: str, start_str: str = None):
    """Границы показа: неделя (7 дней) / месяц (30) / весь план."""
    if start_str:
        start = date.fromisoformat(start_str)
    else:
        days = [d for d in (_slot_day(s) for s in plan.get("slots", [])) if d]
        start = min(days) if days else date.today()
    if span == "week":
        return start, start + timedelta(days=6)
    if span == "month":
        return start, start + timedelta(days=29)
    return None, None


def _rows(plan: dict, span: str, start_str: str = None):
    lo, hi = _window(plan, span, start_str)
    out = []
    for s in plan.get("slots", []):
        day = _slot_day(s)
        if lo and (day is None or day < lo or day > hi):
            continue
        out.append(s)
    return out, lo, hi


def _table_markdown(plan: dict, slots, lo, hi) -> str:
    b = plan.get("brief", {}) or {}
    head = ["# Контент-план",
            "",
            "- период показа: %s" % ("%s — %s" % (lo, hi) if lo else "весь план"),
            "- площадки: %s" % ", ".join(b.get("platforms", []) or ["—"]),
            "- утверждён: %s" % ("ДА (%s, %s)" % (plan.get("approved_by"),
                                                  plan.get("approved_at"))
                                 if plan.get("approved") else "нет"),
            "- окно-вето: %s ч" % b.get("veto_hours"),
            "",
            "| Дата | День | Время | Формат | Рубрика | Куда | Тема | Статус |",
            "|---|---|---|---|---|---|---|---|"]
    for s in slots:
        day = _slot_day(s)
        head.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            s.get("date", "—"),
            _RU_WEEKDAY[day.weekday()] if day else "—",
            (s.get("publish_at") or "??T??:??")[11:16],
            s.get("format", "—"),
            s.get("rubric", "—"),
            ", ".join(s.get("platforms", []) or []),
            (s.get("topic") or "").replace("|", "/"),
            model.label(s.get("status"))))
    if not slots:
        head.append("| — | — | — | — | — | — | в этом окне слотов нет | — |")
    head.append("")
    head.append("_Публикации не происходит: гейт только показывает и логирует "
                "(жёсткий стоп прототипа)._")
    return "\n".join(head)


def cmd_table(a):
    plan = model.load_plan()
    if not plan.get("slots"):
        print("Плана ещё нет. Собери: python -m content_plan.cli plan --brief <файл>")
        return 1
    span = "week" if a.week else ("month" if a.month else "all")
    slots, lo, hi = _rows(plan, span, a.start)

    title = {"week": "План на неделю", "month": "План на месяц",
             "all": "План целиком"}[span]
    print("=== %s ===" % title)
    if lo:
        print("Окно: %s — %s" % (lo, hi))
    print()
    print("%-11s %-3s %-6s %-9s %-14s %-22s %-16s %s"
          % ("дата", "дн", "время", "формат", "рубрика", "куда", "статус", "тема"))
    print("-" * 118)
    for s in slots:
        day = _slot_day(s)
        print("%-11s %-3s %-6s %-9s %-14s %-22s %-16s %s" % (
            s.get("date", "—"),
            _RU_WEEKDAY[day.weekday()] if day else "—",
            (s.get("publish_at") or "??T??:??")[11:16],
            s.get("format", "—"),
            (s.get("rubric") or "—")[:14],
            (", ".join(s.get("platforms", []) or []))[:22],
            model.label(s.get("status")),
            (s.get("topic") or "")[:52]))
    if not slots:
        print("  в этом окне слотов нет")
    print()
    print("Итого в окне: %d слотов. Всего в плане: %d."
          % (len(slots), len(plan["slots"])))
    print("Публикации нет: гейт только показывает и логирует (жёсткий стоп).")

    if a.md is not None:
        out = paths.plan_table_path() if not a.md else paths.data_dir() / a.md
        paths.ensure_dirs()
        out.write_text(_table_markdown(plan, slots, lo, hi), encoding="utf-8")
        print("Markdown-таблица записана: %s" % out)
    return 0


def cmd_board(a):
    from . import gate
    plan = model.load_plan()
    if not plan.get("slots"):
        print("Плана ещё нет.")
        return 1
    print(gate.write_veto_board(plan))
    print()
    print("Файл доски: %s" % paths.veto_board_path())
    return 0


def cmd_approve_slot(a):
    from . import gate
    plan = model.load_plan()
    slot = model.find_slot(plan, a.slot)
    if slot is None:
        print("Нет слота %s." % a.slot)
        return 1
    if not gate.approve_slot(plan, a.slot, who=a.who or "клиент", note=a.note or ""):
        print("Слот %s нельзя утвердить: он в статусе «%s», а утверждать можно "
              "только выпуск «на утверждении» (собранный черновик)."
              % (a.slot, model.label(slot.get("status"))))
        return 1
    model.save_plan(plan)
    print("Выпуск %s УТВЕРЖДЁН (%s)." % (a.slot, a.who or "клиент"))
    print("Публикации всё равно не произойдёт: поверх стоит жёсткий стоп "
          "прототипа — гейт только покажет команду autopost и залогирует её.")
    return 0


def cmd_approve(a):
    plan = model.load_plan()
    if not plan.get("slots"):
        print("Утверждать нечего — плана нет.")
        return 1
    model.approve(plan, who=a.who or "клиент")
    model.save_plan(plan)
    print("План УТВЕРЖДЁН как %s." % (a.who or "клиент"))
    print("Это твоё слово на период: система будет генерить контент по плану "
          "автономно. Каждый пост ты увидишь в окне-вето за %s ч до выхода и "
          "сможешь снять." % plan.get("brief", {}).get("veto_hours"))
    return 0


def cmd_revoke(a):
    plan = model.load_plan()
    model.revoke_approval(plan)
    model.save_plan(plan)
    print("Утверждение снято. Автономная генерация остановлена.")
    return 0


def cmd_veto(a):
    from . import gate
    plan = model.load_plan()
    ok = gate.veto(plan, a.slot, note=a.note or "снято клиентом")
    model.save_plan(plan)
    if ok:
        print("Слот %s снят — он не уйдёт." % a.slot)
        return 0
    print("Не удалось снять слот %s (нет такого или уже опубликован)." % a.slot)
    return 1


def cmd_reslot(a):
    plan = model.load_plan()
    slot = model.find_slot(plan, a.slot)
    if slot is None:
        print("Нет слота %s." % a.slot)
        return 1
    try:
        model.set_status(slot, model.PLANNED, a.note or "возврат в план для правки")
    except ValueError as ex:
        print(str(ex))
        return 1
    slot["artifact"] = None
    slot["generated_at"] = None
    model.save_plan(plan)
    print("Слот %s возвращён в план — перегенерится на следующем прогоне." % a.slot)
    return 0


# --- витрина в телеграм-канале клиента (BOX-26) -----------------------------

def _need_plan():
    plan = model.load_plan()
    if not plan.get("slots"):
        print("Плана ещё нет. Собери: python -m content_plan.cli plan --brief <файл>")
        return None
    return plan


def cmd_showcase(a):
    from . import showcase
    plan = _need_plan()
    if plan is None:
        return 1

    ready, why = showcase.provider_status()
    if not ready:
        print("ВИТРИНА НЕДОСТУПНА: %s" % why)
        print("План цел, ничего не сломалось — единицы просто ждут выкладки.")
        print("Посмотреть их можно таблицей: python -m content_plan.cli table")
        return 1

    slot_ids = [a.slot] if a.slot else None
    rep = showcase.run(plan, send=a.send, slot_ids=slot_ids, limit=a.limit,
                       again=a.again)
    model.save_plan(plan)

    if rep.get("stub"):
        # Заглушка — это НЕ выкладка, и молчать об этом при --send нельзя:
        # человек попросил отправить, а сеть даже не трогалась.
        print("[ЗАГЛУШКА] %s=1 — В TELEGRAM НЕ ОТПРАВЛЕНО НИЧЕГО. Сообщения "
              "записаны файлами в %s, клиент их не видел, статусы слотов не "
              "менялись. Настоящая витрина — сними эту переменную окружения."
              % (showcase.STUB_ENV, showcase.stub_dir()))
    elif not a.send:
        print("[ПРОБНЫЙ ПРОГОН] в Telegram ничего не отправлено. "
              "Реальная выкладка — с флагом --send.")
    total = len(rep["sent"]) + len(rep["dry_run"]) + len(rep["failed"])
    if not total:
        print("Выкладывать нечего: нет единиц в статусе «на утверждении».")
        print("Собрать ВЕСЬ пакет на период заранее (схема BOX-26):")
        print("  python -m content_plan.scheduler --once --lead-days %s"
              % (plan.get("brief", {}).get("horizon_days") or 30))
        return 0
    for res in rep["dry_run"] + rep["sent"]:
        print("  %-34s %-6s %s" % (res["slot"], res["kind"], res["reason"]))
        if res.get("note"):
            print("      (%s)" % res["note"])
    for res in rep["failed"]:
        print("  %-34s ОТКАЗ: %s" % (res["slot"], res.get("reason")))
    print()
    # «Выложено» = ушло в Telegram и клиент это может открыть. Всё остальное —
    # «не отправлено», как бы оно ни называлось внутри (пробный прогон, заглушка).
    print("Ушло клиенту в Telegram: %d, НЕ отправлено: %d, отказов: %d"
          % (len(rep["sent"]), len(rep["dry_run"]), len(rep["failed"])))
    if rep["sent"]:
        print("Клиент листает канал с телефона и говорит правки словами. "
              "Записать правку: python -m content_plan.cli revise --slot ID "
              "--text \"переснять хук\"")
    print("Витрина — это ПОКАЗ клиенту, а не публикация: предохранитель "
          "публикации не тронут.")
    print("Журнал выкладки: %s" % paths.showcase_log_path())
    return 0


def cmd_showcase_status(a):
    from . import config, showcase
    plan = _need_plan()
    if plan is None:
        return 1
    ready, why = showcase.provider_status()
    print("=== Витрина ===")
    print("Канал     : %s" % (config.showcase_chat() or "не задан"))
    if showcase.stub_enabled():
        print("Готовность: ЗАГЛУШКА (%s=1) — в Telegram НЕ уходит ничего, "
              "клиент витрины не видит; сообщения пишутся файлами на диск. "
              "Для настоящей витрины сними эту переменную."
              % showcase.STUB_ENV)
    else:
        print("Готовность: %s" % ("готова" if ready
                                  else "НЕДОСТУПНА — %s" % why))
    tz = showcase.brief_timezone(plan)
    print("Выдача    : ежедневно в %s (%s, UTC%s)"
          % (showcase.brief_post_time(plan), tz, config.timezone_offset(tz)))
    print()
    state = showcase.package_readiness(plan)
    print("Просмотрено клиентом (готовы к одобрению): %d" % len(state["ready"]))
    for sid in state["ready"]:
        print("  + %s" % sid)
    if state["blockers"]:
        print("Мешают одобрить пакет целиком: %d" % len(state["blockers"]))
        for b in state["blockers"]:
            print("  - %-34s %s" % (b["slot"], b["why"]))
    revs = showcase.pending_revisions(plan)
    if revs:
        print()
        print("Правки от клиента (ждут переделки):")
        for s in revs:
            last = (s.get("revisions") or [{}])[-1]
            print("  ! %-34s %s" % (s["id"], last.get("text", "")))
    return 0


def cmd_revise(a):
    from . import showcase
    plan = _need_plan()
    if plan is None:
        return 1
    ok, msg = showcase.revise(plan, a.slot, a.text or "", who=a.who or "клиент")
    if not ok:
        print(msg)
        return 1
    model.save_plan(plan)
    print("Слот %s: %s" % (a.slot, msg))
    print("Статус — «есть правки от клиента»: этот выпуск не одобрится ни "
          "вручную, ни авто, пока правку не отработают.")
    print("Пересобрать заново : python -m content_plan.cli revise-done --slot %s "
          "--regenerate" % a.slot)
    print("Правка внесена     : python -m content_plan.cli revise-done --slot %s"
          % a.slot)
    return 0


def cmd_revise_done(a):
    from . import showcase
    plan = _need_plan()
    if plan is None:
        return 1
    ok, msg = showcase.revise_done(plan, a.slot, note=a.note or "",
                                   regenerate=a.regenerate)
    if not ok:
        print(msg)
        return 1
    model.save_plan(plan)
    print(msg)
    return 0


def cmd_approve_package(a):
    from . import showcase
    plan = _need_plan()
    if plan is None:
        return 1
    ok, state = showcase.approve_package(plan, who=a.who or "клиент",
                                         force=a.force)
    if not ok:
        print("ПАКЕТ НЕ ОДОБРЕН: не всё готово к полному одобрению.")
        for b in state["blockers"]:
            print("  - %-34s %s" % (b["slot"], b["why"]))
        print()
        print("«Одобрить весь пакет» должно значить, что человек видел ВСЁ. "
              "Доделай перечисленное — или, если решение осознанное, повтори "
              "с --force (одобрятся только просмотренные единицы).")
        return 1
    model.save_plan(plan)
    print("ПАКЕТ ОДОБРЕН ЦЕЛИКОМ как %s. Утверждено единиц: %d."
          % (a.who or "клиент", len(state["ready"])))
    print("Это слово человека на весь период. Дальше очередь выдаётся ежедневно "
          "в одно и то же время.")
    print("Расписание выдачи: python -m content_plan.cli delivery")
    print("Публикации всё равно не будет: поверх стоит жёсткий стоп прототипа.")
    return 0


def cmd_delivery(a):
    from . import showcase
    plan = _need_plan()
    if plan is None:
        return 1
    sched = showcase.delivery_schedule(plan, start=a.start)
    print("=== Ежедневная выдача ===")
    print("Время : %s (%s, UTC%s) — одно и то же каждый день"
          % (sched["post_time"], sched["timezone"], sched["offset"]))
    print("Старт : %s, по одной единице в день" % sched["start"])
    print()
    print("%-4s %-11s %-26s %-9s %-14s %s"
          % ("#", "дата", "время с зоной", "формат", "рубрика", "слот"))
    print("-" * 100)
    for r in sched["rows"]:
        print("%-4d %-11s %-26s %-9s %-14s %s"
              % (r["n"], r["date"], r["publish_at_tz"], r["format"],
                 (r["rubric"] or "—")[:14], r["slot"]))
    if not sched["rows"]:
        print("  очередь пуста")
    print()
    for line in sched["verdict"]:
        print("! " + line)

    if a.apply:
        changed = showcase.apply_delivery(plan, sched)
        model.save_plan(plan)
        print()
        print("Перенесено на даты выдачи: %d слотов (статусы не менялись)."
              % changed)
    if a.md:
        showcase.write_delivery_md(plan, sched)
        print("Расписание записано: %s" % paths.delivery_plan_path())
    print()
    print("Публикации не происходит: жёсткий стоп прототипа на месте — гейт "
          "только покажет команду autopost и залогирует её.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Управление контент-планом.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("questions", help="показать интервью планировщика")
    sub.add_parser("example-brief", help="записать шаблон brief.example.json")

    p_int = sub.add_parser("interview", help="записать ответы интервью в brief.json")
    p_int.add_argument("--set", action="append", metavar="КЛЮЧ=ЗНАЧЕНИЕ",
                       help="ответ на вопрос (можно повторять)")
    p_int.add_argument("--fresh", action="store_true",
                       help="не подкладывать прошлые ответы — начать с умолчаний")

    p_plan = sub.add_parser("plan", help="собрать план из brief.json")
    p_plan.add_argument("--brief", required=True, help="путь к brief.json")

    sub.add_parser("status", help="сводка плана")

    p_tbl = sub.add_parser("table", help="таблица плана (неделя/месяц/весь)")
    g_span = p_tbl.add_mutually_exclusive_group()
    g_span.add_argument("--week", action="store_true", help="окно 7 дней")
    g_span.add_argument("--month", action="store_true", help="окно 30 дней")
    p_tbl.add_argument("--start", help="начало окна YYYY-MM-DD (по умолчанию "
                                       "первый день плана)")
    p_tbl.add_argument("--md", nargs="?", const="", metavar="ИМЯ",
                       help="выгрузить markdown-таблицу в папку данных модуля")

    sub.add_parser("board", help="доска окна-вето: что скоро уйдёт")

    p_ap = sub.add_parser("approve", help="утвердить план (слово на период)")
    p_ap.add_argument("--who", help="кто утвердил (для лога)")

    sub.add_parser("revoke", help="снять утверждение")

    p_aps = sub.add_parser("approve-slot", help="утвердить конкретный выпуск")
    p_aps.add_argument("--slot", required=True, help="id слота")
    p_aps.add_argument("--who", help="кто утвердил (для лога)")
    p_aps.add_argument("--note", help="комментарий")

    p_veto = sub.add_parser("veto", help="снять слот (не уйдёт)")
    p_veto.add_argument("--slot", required=True, help="id слота")
    p_veto.add_argument("--note", help="комментарий")

    p_res = sub.add_parser("reslot", help="вернуть слот в план для правки")
    p_res.add_argument("--slot", required=True, help="id слота")
    p_res.add_argument("--note", help="комментарий")

    # --- витрина (BOX-26) ---
    p_show = sub.add_parser("showcase",
                            help="выложить готовые единицы в телеграм-витрину")
    p_show.add_argument("--send", action="store_true",
                        help="РЕАЛЬНО отправить в канал. Без флага — пробный "
                             "прогон: печатается, что ушло бы, но запрос к "
                             "Telegram НЕ делается")
    p_show.add_argument("--slot", help="только этот слот")
    p_show.add_argument("--limit", type=int, default=None,
                        help="не больше N единиц за прогон")
    p_show.add_argument("--again", action="store_true",
                        help="перевыложить и то, что уже показано")

    sub.add_parser("showcase-status", help="что в витрине и где правки")

    p_rev = sub.add_parser("revise", help="записать правку клиента по слоту")
    p_rev.add_argument("--slot", required=True, help="id слота из подписи в канале")
    p_rev.add_argument("--text", required=True, help="что переделать, словами клиента")
    p_rev.add_argument("--who", help="кто сказал (для лога)")

    p_rvd = sub.add_parser("revise-done", help="правка отработана")
    p_rvd.add_argument("--slot", required=True, help="id слота")
    p_rvd.add_argument("--note", help="комментарий")
    p_rvd.add_argument("--regenerate", action="store_true",
                       help="не просто пометить, а пересобрать слот с нуля")

    p_pkg = sub.add_parser("approve-package",
                           help="одобрить ВЕСЬ пакет целиком (слово человека)")
    p_pkg.add_argument("--who", help="кто одобрил (для лога)")
    p_pkg.add_argument("--force", action="store_true",
                       help="одобрить просмотренное, даже если готово не всё")

    p_del = sub.add_parser("delivery", help="расписание ежедневной выдачи")
    p_del.add_argument("--start", help="первый день выдачи YYYY-MM-DD "
                                       "(по умолчанию завтра)")
    p_del.add_argument("--apply", action="store_true",
                       help="перенести одобренные слоты на даты выдачи")
    p_del.add_argument("--md", action="store_true",
                       help="выгрузить расписание в delivery.md")

    a = ap.parse_args(argv)
    handlers = {
        "questions": cmd_questions, "example-brief": cmd_example_brief,
        "interview": cmd_interview, "plan": cmd_plan, "status": cmd_status,
        "table": cmd_table, "board": cmd_board, "approve": cmd_approve,
        "revoke": cmd_revoke, "approve-slot": cmd_approve_slot,
        "veto": cmd_veto, "reslot": cmd_reslot,
        "showcase": cmd_showcase, "showcase-status": cmd_showcase_status,
        "revise": cmd_revise, "revise-done": cmd_revise_done,
        "approve-package": cmd_approve_package, "delivery": cmd_delivery,
    }
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
