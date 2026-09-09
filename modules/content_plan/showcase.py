# -*- coding: utf-8 -*-
"""Витрина: личный телеграм-канал клиента как архив и смотровая площадка.

Схема BOX-26 (решение владельца, реализовано как описано):

  1. Клиент выбирает горизонт (неделя / две / три) — brief.horizon_days.
  2. Завод генерит ВЕСЬ контент на период заранее (executor.py).
  3. Готовое уходит В ЛИЧНЫЙ ТЕЛЕГРАМ-КАНАЛ клиента — по одному сообщению на
     единицу, с понятной подписью: дата планируемой публикации, площадки,
     рубрика/тема и служебный идентификатор слота.
  4. Клиент листает канал с телефона, возвращается в Claude Code и говорит
     правки СЛОВАМИ. Claude фиксирует их командами `revise` — телеграм витрина,
     Claude интерфейс правок.
  5. После одобрения ВСЕГО пакета (`approve-package`) очередь выдаётся
     ЕЖЕДНЕВНО в одно и то же время (`delivery`).
  6. Время выдачи выбирает человек, вместе с часовым поясом. Хранится С ЗОНОЙ.

ЧТО ЭТО НЕ ЕСТЬ. Витрина — ОТДЕЛЬНЫЙ канал показа клиенту, а не публикация.
Предохранитель публикации (gate.REAL_PUBLISH_IMPLEMENTED=False) она не трогает
и не может тронуть: витрина не знает про autopost и никогда не переводит слот в
`published`. Максимум, что она делает со статусом, — `generated → showcased`
(«клиент это увидел»).

ПРЕДОХРАНИТЕЛЬ ВИТРИНЫ (свой, отдельный). Отправка сообщений в телеграм — это
внешнее действие, поэтому здесь тот же закон, что у tg_send.py и autopost.py:
без явного `--send` идёт ПРОБНЫЙ ПРОГОН. Dry-run НЕ меняет статус слота: клиент
ничего не увидел — значит, «показано» врать нельзя.

ОФЛАЙН. Клиент — Windows без сети в момент установки. Нет `requests`, нет
токена, нет канала, сеть не отвечает — модуль НЕ падает: возвращает честное
«витрина недоступна» с причиной, слот остаётся в прежнем статусе, план цел.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from . import config, model, paths

API = "https://api.telegram.org"

# Потолки Telegram (замерено живьём, см. autopost.py LIMITS): подпись к медиа —
# 1024 символа, отдельное текстовое сообщение — 4096. Превышение подписи Telegram
# не обрезает, а ОТКАЗЫВАЕТ по 400, поэтому длинный хвост уходит отдельным
# сообщением-ответом, а не режется молча.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096

# Заглушка провайдера для прогонов на фикстурах: сеть НЕ трогается вовсе,
# сообщения пишутся в content_plan/showcase_stub/.
#
# ⚠️ ЗАГЛУШКА НЕ СЧИТАЕТСЯ ВЫКЛАДКОЙ. Раньше она возвращала sent=True, слот
# уезжал в статус «выложено в витрину, ждёт просмотра клиентом», а отчёт бодро
# писал «Выложено: N» — при том что в Telegram не ушло ни одного байта, файл лёг
# в папку рядом. Клиент (и Claude, читающий отчёт) считал материал показанным.
# Теперь заглушка = тот же пробный прогон: sent=False, статус слота НЕ меняется,
# в отчёте прямым текстом «в Telegram НЕ отправлено». Правило одно на весь файл:
# «показано» пишем только про то, что человек действительно мог увидеть.
STUB_ENV = "CONTENT_PLAN_SHOWCASE_STUB"

_RU_MONTH = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
             "августа", "сентября", "октября", "ноября", "декабря")
_RU_WEEKDAY = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_FORMAT_LABEL = {"carousel": "КАРУСЕЛЬ", "reels": "РИЛС", "stories": "СТОРИС"}


def stub_enabled() -> bool:
    val = os.environ.get(STUB_ENV)
    return bool(val) and val.strip().lower() in ("1", "true", "yes", "да", "on")


def stub_dir():
    return paths.data_dir() / "showcase_stub"


# --- время с зоной ---------------------------------------------------------

def brief_timezone(plan: dict) -> str:
    b = (plan or {}).get("brief", {}) or {}
    raw = b.get("post_timezone")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return config.default_timezone()


def brief_post_time(plan: dict) -> str:
    b = (plan or {}).get("brief", {}) or {}
    raw = b.get("post_time")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return config.default_post_time()


def aware(dt_naive: datetime, tz_name: str) -> datetime:
    """Наивное местное время слота → время С ЗОНОЙ клиента."""
    return dt_naive.replace(tzinfo=config.tzinfo_for(tz_name))


def publish_at_tz(slot: dict, tz_name: str):
    """ISO-строка времени публикации со смещением: 2026-08-05T09:00:00+03:00."""
    raw = slot.get("publish_at")
    try:
        naive = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    return aware(naive, tz_name).isoformat()


def human_when(slot: dict, tz_name: str) -> str:
    """«5 августа (вт), 09:00 (Europe/Moscow, UTC+03:00)» — как человек читает."""
    raw = slot.get("publish_at")
    try:
        naive = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return "дата не разобрана"
    return "%d %s (%s), %02d:%02d (%s, UTC%s)" % (
        naive.day, _RU_MONTH[naive.month - 1], _RU_WEEKDAY[naive.weekday()],
        naive.hour, naive.minute, tz_name, config.timezone_offset(tz_name))


# --- подпись единицы -------------------------------------------------------

def caption(slot: dict, plan: dict) -> str:
    """Подпись к единице в витрине. Обязательное по ТЗ: дата планируемой
    публикации, площадки, рубрика/тема и служебный идентификатор слота, по
    которому клиент потом сошлётся словами."""
    tz_name = brief_timezone(plan)
    fmt = _FORMAT_LABEL.get(slot.get("format"), (slot.get("format") or "?").upper())
    lines = [
        "%s · %s" % (fmt, human_when(slot, tz_name)),
        "Рубрика: %s" % (slot.get("rubric") or "—"),
        "Куда пойдёт: %s" % (", ".join(slot.get("platforms") or []) or "—"),
        "",
        "Тема: %s" % (slot.get("topic") or "—"),
    ]
    if slot.get("hook"):
        lines.append("Хук: %s" % slot["hook"])
    if slot.get("needs_input"):
        lines.append("")
        lines.append("! Черновик собран не полностью — нужен вход: %s"
                     % slot["needs_input"])
    revisions = slot.get("revisions") or []
    if revisions:
        lines.append("")
        lines.append("Правок от тебя по этой единице: %d (последняя: %s)"
                     % (len(revisions), revisions[-1].get("text", "")))
    lines += [
        "",
        "Слот: %s" % slot.get("id"),
        "Что-то поправить — скажи Клоду словами, например:",
        "«в слоте %s переснять хук»" % slot.get("id"),
    ]
    return "\n".join(lines)


def _split_caption(text: str):
    """(подпись_к_медиа, хвост_отдельным_сообщением). Telegram по 400 ОТКАЗЫВАЕТ
    на длинной подписи, поэтому режем сами и хвост шлём вторым сообщением."""
    if len(text) <= CAPTION_LIMIT:
        return text, None
    head = text[:CAPTION_LIMIT - 20].rstrip() + "\n…продолжение ниже"
    return head, text[len(head):][:MESSAGE_LIMIT]


# --- медиа из очереди ------------------------------------------------------

def unit_media(slot: dict):
    """Что физически показывать. Читает артефакт очереди (executor положил его в
    content_plan/queue/<slot>.json). Возвращает:
        {"kind": "album",   "files": [Path, ...]}   — карусель альбомом
        {"kind": "video",   "file": Path}           — рилс
        {"kind": "text"}                            — медиа нет (dry-run-черновик)
    Отсутствующий/битый артефакт — не ошибка: покажем текстом, честно."""
    name = slot.get("artifact")
    if not name:
        return {"kind": "text", "why": "артефакта в очереди нет (слот не собран)"}
    path = paths.queue_dir() / name
    if not path.exists():
        return {"kind": "text", "why": "файл очереди %s не найден" % name}
    try:
        with path.open(encoding="utf-8-sig") as fh:
            art = json.load(fh)
    except (ValueError, OSError) as ex:
        return {"kind": "text", "why": "артефакт не прочитан: %s" % ex}

    media = art.get("media")
    if not isinstance(media, dict):
        return {"kind": "text",
                "why": art.get("needs_input")
                or "черновик собран в режиме dry-run — медиа ещё нет"}

    base = paths.queue_dir() / (media.get("dir") or slot.get("id") or "")
    if media.get("kind") == "carousel":
        files = [base / s for s in (media.get("slides") or [])]
        files = [f for f in files if f.exists()]
        if 2 <= len(files) <= 10:
            return {"kind": "album", "files": files}
        if len(files) == 1:
            return {"kind": "photo", "file": files[0]}
        return {"kind": "text",
                "why": "слайдов на диске %d — альбомом Telegram примет 2–10"
                       % len(files)}
    if media.get("kind") == "reels":
        f = base / (media.get("video") or "reels.mp4")
        if f.exists():
            return {"kind": "video", "file": f}
        return {"kind": "text", "why": "видео %s не найдено на диске" % f.name}
    return {"kind": "text", "why": "неизвестный вид медиа %r" % media.get("kind")}


# --- провайдер отправки ----------------------------------------------------
#
# Путь ТОТ ЖЕ, которым завод уже кладёт готовое в телеграм-архив: прямой Bot API
# своим ботом (tg_send.py) — sendMediaGroup / sendVideo / sendPhoto / sendMessage,
# TG_BOT_TOKEN и TG_CHAT_ID из .env. Второго клиента не заводим: методы, ключи и
# закон «наружу только по --send» здесь ровно те же. Обоснование выбора именно
# этого пути, а не Upload-Post (autopost.py), — в docs/journals/C9-veto.md.

def _requests():
    """Ленивый импорт requests: без сети/без пакета модуль не должен падать."""
    try:
        import requests
        return requests, None
    except ImportError:
        return None, ("библиотека requests не установлена — витрина недоступна "
                      "(offline-режим). Ставится штатно: requests==2.32.5")


def provider_status():
    """Готова ли витрина. Возвращает (готова, причина_если_нет). Секретов не
    раскрывает — только факт наличия.

    При включённой заглушке «готова» означает ровно одно: вызов провайдера
    пройдёт без падения. Клиенту это НЕ витрина — см. stub_enabled(); тот, кто
    печатает результат человеку (cli, doctor), обязан спросить про заглушку
    отдельно и сказать о ней вслух.
    """
    if stub_enabled():
        return True, None
    if not config.showcase_token():
        return False, ("нет %s — витрина недоступна. Токен бота кладётся в .env "
                       "(не в код, не в чат)." % config.SHOWCASE_TOKEN_KEY)
    if not config.showcase_chat():
        return False, ("нет канала-витрины — витрина недоступна. Пропиши в .env "
                       "%s=@твой_канал (бот должен быть в нём администратором)."
                       % config.SHOWCASE_CHAT_KEYS[0])
    ok, why = _requests()
    if ok is None:
        return False, why
    return True, None


def _stub_send(kind: str, chat: str, text: str, files, slot_id: str) -> dict:
    """Заглушка провайдера: НИ ОДНОГО сетевого вызова, всё на диск.

    Возвращает `sent=False` НАМЕРЕННО: файл на диске завода — это не сообщение в
    телефоне клиента. `message_ids` не выдумываем вовсе: несуществующий
    «stub-<slot>» попадал в план и выглядел там как настоящий след отправки.
    """
    d = stub_dir()
    d.mkdir(parents=True, exist_ok=True)
    body = ["[ЗАГЛУШКА ВИТРИНЫ — сети не было, клиент этого не видел]",
            "тип: %s" % kind, "канал: %s" % chat,
            "файлы: %s" % (", ".join(f.name for f in (files or [])) or "—"),
            "", text]
    path = d / ("%s.txt" % slot_id)
    path.write_text("\n".join(body), encoding="utf-8")
    return {"ok": True, "stub": True, "sent": False, "kind": kind,
            "path": path, "message_ids": []}


def _post(requests_mod, token: str, method: str, data: dict, files=None,
          timeout: int = 180) -> dict:
    url = "%s/bot%s/%s" % (API, token, method)
    resp = requests_mod.post(url, data=data, files=files, timeout=timeout)
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "reason": "HTTP %s от Telegram (ответ не JSON)"
                                       % resp.status_code}
    if not payload.get("ok"):
        return {"ok": False, "reason": "Telegram отказал: %s"
                                       % payload.get("description", payload)}
    return {"ok": True, "result": payload.get("result")}


def send_unit(slot: dict, plan: dict, send: bool = False) -> dict:
    """Выложить ОДНУ единицу в витрину. Без send=True — пробный прогон.

    Возвращает отчёт-словарь; исключений наружу не выпускает — обрыв сети не
    должен ронять план (офлайн-клиент)."""
    text = caption(slot, plan)
    media = unit_media(slot)
    chat = config.showcase_chat() if not stub_enabled() else "(заглушка)"
    report = {"slot": slot.get("id"), "kind": media["kind"], "chat": chat,
              "caption_len": len(text), "sent": False, "dry_run": not send}
    if media.get("why"):
        report["note"] = media["why"]

    ready, why = provider_status()
    if not ready:
        report["ok"] = False
        report["reason"] = why
        return report

    if not send:
        report["ok"] = True
        report["reason"] = ("пробный прогон: в Telegram НЕ отправлено. "
                            "Реальная выкладка — с флагом --send")
        return report

    head, tail = _split_caption(text)
    files = media.get("files") or ([media["file"]] if media.get("file") else [])

    if stub_enabled():
        res = _stub_send(media["kind"], chat, text, files, slot.get("id") or "?")
        report.update({"ok": True, "sent": False, "stub": True,
                       "message_ids": [],
                       "reason": "ЗАГЛУШКА ВИТРИНЫ (%s=1): в Telegram НЕ "
                                 "отправлено, сообщение записано файлом %s. "
                                 "Клиент этого не увидел — слот остаётся в "
                                 "прежнем статусе."
                                 % (STUB_ENV, res["path"].name)})
        return report

    requests_mod, why = _requests()
    if requests_mod is None:
        report["ok"] = False
        report["reason"] = why
        return report
    token = config.showcase_token()

    handles = []
    try:
        if media["kind"] == "album":
            items, payload_files = [], {}
            for i, f in enumerate(media["files"]):
                key = "f%d" % i
                item = {"type": "photo", "media": "attach://%s" % key}
                if i == 0:
                    item["caption"] = head
                items.append(item)
                fh = f.open("rb")
                handles.append(fh)
                payload_files[key] = fh
            res = _post(requests_mod, token, "sendMediaGroup",
                        {"chat_id": chat, "media": json.dumps(items,
                                                              ensure_ascii=False)},
                        files=payload_files)
        elif media["kind"] == "video":
            fh = media["file"].open("rb")
            handles.append(fh)
            res = _post(requests_mod, token, "sendVideo",
                        {"chat_id": chat, "caption": head,
                         "supports_streaming": "true"},
                        files={"video": fh})
        elif media["kind"] == "photo":
            fh = media["file"].open("rb")
            handles.append(fh)
            res = _post(requests_mod, token, "sendPhoto",
                        {"chat_id": chat, "caption": head}, files={"photo": fh})
        else:
            res = _post(requests_mod, token, "sendMessage",
                        {"chat_id": chat, "text": head[:MESSAGE_LIMIT]})
    except Exception as ex:  # обрыв сети/таймаут/файл исчез — не роняем план
        report["ok"] = False
        report["reason"] = ("витрина недоступна: %s: %s"
                            % (type(ex).__name__, ex))
        return report
    finally:
        for fh in handles:
            try:
                fh.close()
            except OSError:
                pass

    if not res.get("ok"):
        report["ok"] = False
        report["reason"] = res.get("reason", "неизвестный отказ Telegram")
        return report

    result = res.get("result")
    ids = ([m.get("message_id") for m in result]
           if isinstance(result, list) else [(result or {}).get("message_id")])

    if tail:
        try:
            _post(requests_mod, token, "sendMessage",
                  {"chat_id": chat, "text": tail})
        except Exception:
            pass  # хвост подписи не критичен: единица уже в канале

    report.update({"ok": True, "sent": True, "message_ids": ids,
                   "reason": "выложено в витрину"})
    return report


# --- выкладка пакета -------------------------------------------------------

def _log(entry: dict) -> None:
    paths.ensure_dirs()
    with paths.showcase_log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def eligible_slots(plan: dict, slot_ids=None, again: bool = False):
    """Что можно выкладывать: собранные черновики (`generated`). С `again` —
    ещё и уже показанные (перевыкладка после правки)."""
    want = {model.GENERATED} | ({model.SHOWCASED} if again else set())
    out = []
    for s in plan.get("slots", []):
        if slot_ids and s.get("id") not in slot_ids:
            continue
        if s.get("status") in want:
            out.append(s)
    return out


def run(plan: dict, send: bool = False, slot_ids=None, limit: int = None,
        again: bool = False) -> dict:
    """Выложить пакет в витрину. Меняет plan на месте ТОЛЬКО при реальной
    отправке: показанным считается лишь то, что клиент действительно увидел.

    Три случая, когда статус слота НЕ трогаем: пробный прогон, заглушка и любой
    отказ. Заглушка проверяется ЗДЕСЬ ещё раз, поверх send_unit: единственная
    запись, из которой потом читают «клиент это видел», не должна зависеть от
    того, не забыл ли кто-то выставить sent=False уровнем ниже."""
    report = {"sent": [], "dry_run": [], "failed": [], "provider_ok": True,
              "provider_reason": None, "stub": stub_enabled()}
    ready, why = provider_status()
    report["provider_ok"], report["provider_reason"] = ready, why

    made = 0
    for slot in eligible_slots(plan, slot_ids, again):
        if limit is not None and made >= limit:
            break
        res = send_unit(slot, plan, send=send)
        made += 1
        _log({"at": model.now_iso(), "slot": slot.get("id"),
              "sent": res.get("sent"), "ok": res.get("ok"),
              "kind": res.get("kind"), "reason": res.get("reason"),
              "message_ids": res.get("message_ids")})
        if not res.get("ok"):
            report["failed"].append(res)
            continue
        if not res.get("sent") or res.get("stub"):
            report["dry_run"].append(res)
            continue
        slot["showcase"] = {"at": model.now_iso(), "chat": res.get("chat"),
                            "message_ids": res.get("message_ids"),
                            "kind": res.get("kind"), "stub": res.get("stub", False)}
        if slot.get("status") == model.GENERATED:
            model.set_status(slot, model.SHOWCASED,
                             "выложено в витрину (%s), ждёт просмотра клиентом"
                             % res.get("kind"))
        else:
            model.add_history(slot, "showcase:again", "перевыложено в витрину")
        report["sent"].append(res)
    return report


# --- приём обратной связи от клиента ---------------------------------------

def revise(plan: dict, slot_id: str, text: str, who: str = "клиент"):
    """Зафиксировать правку клиента по слоту. Это и есть шов «Claude — интерфейс
    правок»: клиент говорит словами, Claude кладёт это сюда.

    Возвращает (ок, сообщение). Слот уходит в статус «есть правки от клиента»:
    такой слот НЕ одобряется ни вручную, ни авто, пока правку не отработают."""
    slot = model.find_slot(plan, slot_id)
    if slot is None:
        return False, "нет слота %s" % slot_id
    if slot.get("status") == model.PUBLISHED:
        return False, "слот %s уже опубликован — правку вносить некуда" % slot_id
    if not (text or "").strip():
        return False, "пустая правка: скажи, что именно переделать"
    slot.setdefault("revisions", []).append(
        {"at": model.now_iso(), "who": who, "text": text.strip(), "done": False})
    if slot.get("status") != model.REVISING:
        model.set_status(slot, model.REVISING, "правка клиента: %s" % text.strip())
    else:
        model.add_history(slot, "revision:add", text.strip())
    return True, "правка записана: %s" % text.strip()


def revise_done(plan: dict, slot_id: str, note: str = "",
                regenerate: bool = False):
    """Правка отработана. По умолчанию черновик считается поправленным и слот
    возвращается «на утверждение» (готов к перевыкладке в витрину).
    С regenerate=True — уходит в черновики плана и собирается заново."""
    slot = model.find_slot(plan, slot_id)
    if slot is None:
        return False, "нет слота %s" % slot_id
    if slot.get("status") != model.REVISING:
        return False, ("слот %s не в правках (сейчас «%s»)"
                       % (slot_id, model.label(slot.get("status"))))
    for rev in slot.get("revisions") or []:
        rev["done"] = True
    if regenerate:
        model.set_status(slot, model.PLANNED,
                         note or "правка принята — слот перегенерится")
        slot["artifact"] = None
        slot["generated_at"] = None
        return True, ("слот %s возвращён в план: перегенерится на следующем "
                      "прогоне исполнителя" % slot_id)
    model.set_status(slot, model.GENERATED,
                     note or "правка внесена — готов к перевыкладке в витрину")
    return True, ("слот %s снова на утверждении: выложи его в витрину заново "
                  "(showcase --slot %s --send)" % (slot_id, slot_id))


def pending_revisions(plan: dict):
    """Слоты, по которым есть неотработанные правки клиента."""
    return [s for s in plan.get("slots", []) if s.get("status") == model.REVISING]


# --- одобрение всего пакета целиком ----------------------------------------

def package_readiness(plan: dict) -> dict:
    """Готов ли пакет к одобрению целиком. Только СМОТРИТ, ничего не меняет."""
    ready, blockers, skipped = [], [], []
    for s in plan.get("slots", []):
        st = s.get("status")
        if st in (model.VETOED, model.FAILED, model.PUBLISHED):
            skipped.append(s["id"])
        elif st == model.SHOWCASED:
            ready.append(s["id"])
        elif st == model.APPROVED:
            skipped.append(s["id"])
        elif st == model.REVISING:
            blockers.append({"slot": s["id"], "why": "есть правки от клиента — "
                                                     "сначала переделай"})
        elif st == model.GENERATED:
            blockers.append({"slot": s["id"], "why": "собран, но НЕ выложен в "
                                                     "витрину — клиент его не видел"})
        else:  # planned
            blockers.append({"slot": s["id"], "why": "черновик ещё не собран"})
    return {"ready": ready, "blockers": blockers, "skipped": skipped}


def approve_package(plan: dict, who: str = "клиент", force: bool = False):
    """Слово человека на ВЕСЬ пакет: «одобряю всё, публикуй по расписанию».

    Утверждает план целиком и переводит все просмотренные единицы в `approved`.
    Публикацию НЕ включает: поверх стоит жёсткий стоп gate.py — очередь просто
    становится готовой к ежедневной выдаче.

    Без force одобрение НЕ проходит, пока есть неготовые единицы: «полное
    одобрение» должно означать, что человек видел всё, а не почти всё."""
    state = package_readiness(plan)
    if state["blockers"] and not force:
        return False, state
    for slot_id in state["ready"]:
        slot = model.find_slot(plan, slot_id)
        model.set_status(slot, model.APPROVED,
                         "одобрен в составе пакета (%s)" % who)
        slot["approved_by"] = who
        slot["approved_at"] = model.now_iso()
    model.approve(plan, who=who)
    plan["package_approved_at"] = model.now_iso()
    return True, state


# --- расписание ежедневной выдачи ------------------------------------------

def delivery_queue(plan: dict):
    """Очередь одобренного в порядке исходного плана — что выдавать по дням."""
    slots = [s for s in plan.get("slots", []) if s.get("status") == model.APPROVED]
    return sorted(slots, key=lambda s: (s.get("publish_at") or "", s.get("id") or ""))


def delivery_schedule(plan: dict, start=None, now: datetime = None) -> dict:
    """Разложить одобренную очередь ПО ОДНОЙ ЕДИНИЦЕ В ДЕНЬ в выбранное время
    с учётом часового пояса.

    Честность про длину: если единиц больше, чем дней в горизонте — очередь
    растягивается за пределы периода, и мы называем дату, до которой; если
    меньше — прямо говорим, сколько дней останется без контента."""
    now = now or datetime.now()
    b = (plan or {}).get("brief", {}) or {}
    tz_name = brief_timezone(plan)
    post_time = brief_post_time(plan)
    try:
        hh, _, mm = post_time.partition(":")
        hour, minute = int(hh), int(mm or 0)
    except ValueError:
        hour, minute = 9, 0
    horizon = b.get("horizon_days")
    horizon = int(horizon) if str(horizon).isdigit() else None

    if start is None:
        first = now.date() + timedelta(days=1)   # выдача стартует со следующего дня
    elif isinstance(start, str):
        first = date.fromisoformat(start)
    else:
        first = start

    queue = delivery_queue(plan)
    rows = []
    for i, slot in enumerate(queue):
        day = first + timedelta(days=i)
        naive = datetime(day.year, day.month, day.day, hour, minute)
        rows.append({"n": i + 1, "date": day.isoformat(),
                     "publish_at": naive.replace(microsecond=0).isoformat(),
                     "publish_at_tz": aware(naive, tz_name).isoformat(),
                     "slot": slot.get("id"), "format": slot.get("format"),
                     "rubric": slot.get("rubric"), "topic": slot.get("topic"),
                     "platforms": list(slot.get("platforms") or [])})

    verdict = []
    if not rows:
        verdict.append("Одобренных единиц нет — выдавать нечего. Сначала: "
                       "выкладка в витрину, правки, одобрение пакета.")
    elif horizon:
        if len(rows) > horizon:
            verdict.append(
                "Единиц (%d) БОЛЬШЕ, чем дней в горизонте (%d): очередь "
                "растягивается — выдача займёт %d дней и закончится %s, то есть "
                "на %d дн. позже конца периода."
                % (len(rows), horizon, len(rows), rows[-1]["date"],
                   len(rows) - horizon))
        elif len(rows) < horizon:
            verdict.append(
                "Единиц (%d) МЕНЬШЕ, чем дней в горизонте (%d): контента хватит "
                "до %s, дальше %d дн. без публикаций. Либо догенерируй %d единиц, "
                "либо честно планируй период короче."
                % (len(rows), horizon, rows[-1]["date"], horizon - len(rows),
                   horizon - len(rows)))
        else:
            verdict.append("Единиц ровно на горизонт: %d шт. на %d дней, "
                           "по одной в день до %s."
                           % (len(rows), horizon, rows[-1]["date"]))
    else:
        verdict.append("Горизонт в брифе не задан: разложил %d единиц по одной "
                       "в день до %s." % (len(rows), rows[-1]["date"]))

    return {"rows": rows, "verdict": verdict, "timezone": tz_name,
            "offset": config.timezone_offset(tz_name), "post_time": post_time,
            "horizon_days": horizon, "start": first.isoformat()}


def apply_delivery(plan: dict, schedule: dict) -> int:
    """Перенести одобренные слоты на даты ежедневной выдачи (publish_at,
    publish_at_tz, окно-вето). Статусы НЕ трогает — публикацию не приближает."""
    b = (plan or {}).get("brief", {}) or {}
    try:
        veto_hours = int(b.get("veto_hours") or 0)
    except (TypeError, ValueError):
        veto_hours = 0
    changed = 0
    for row in schedule.get("rows", []):
        slot = model.find_slot(plan, row["slot"])
        if slot is None:
            continue
        naive = datetime.strptime(row["publish_at"], "%Y-%m-%dT%H:%M:%S")
        slot["date"] = row["date"]
        slot["time"] = schedule.get("post_time")
        slot["publish_at"] = row["publish_at"]
        slot["publish_at_tz"] = row["publish_at_tz"]
        slot["timezone"] = schedule.get("timezone")
        slot["veto_opens_at"] = (naive - timedelta(hours=veto_hours)) \
            .replace(microsecond=0).isoformat()
        model.add_history(slot, "delivery:scheduled",
                          "ежедневная выдача: %s (%s)"
                          % (row["publish_at_tz"], schedule.get("timezone")))
        changed += 1
    return changed


def write_delivery_md(plan: dict, schedule: dict) -> str:
    """Расписание выдачи человеческим текстом — можно показать клиенту."""
    paths.ensure_dirs()
    lines = ["# Ежедневная выдача", "",
             "_Обновлено: %s_" % model.now_iso(), "",
             "- время выдачи: %s (%s, UTC%s)" % (schedule["post_time"],
                                                 schedule["timezone"],
                                                 schedule["offset"]),
             "- старт: %s, по одной единице в день" % schedule["start"], ""]
    for line in schedule["verdict"]:
        lines.append("> " + line)
    lines += ["", "| # | Дата | Время с зоной | Формат | Рубрика | Куда | Тема | Слот |",
              "|---|---|---|---|---|---|---|---|"]
    for r in schedule["rows"]:
        lines.append("| %d | %s | %s | %s | %s | %s | %s | `%s` |" % (
            r["n"], r["date"], r["publish_at_tz"], r["format"], r["rubric"],
            ", ".join(r["platforms"]), (r["topic"] or "").replace("|", "/"),
            r["slot"]))
    if not schedule["rows"]:
        lines.append("| — | — | — | — | — | — | очередь пуста | — |")
    lines += ["", "_Публикации не происходит: поверх стоит жёсткий стоп "
                  "прототипа (gate.REAL_PUBLISH_IMPLEMENTED=False). Гейт только "
                  "покажет команду autopost и залогирует её._"]
    text = "\n".join(lines)
    paths.delivery_plan_path().write_text(text, encoding="utf-8")
    return text
