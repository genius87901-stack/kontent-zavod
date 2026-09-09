# -*- coding: utf-8 -*-
"""Узнать идентификатор канала MAX, куда бот будет класть сторис.

ЗАЧЕМ ОТДЕЛЬНАЯ ПРОГРАММА. Настройка MAX упирается в один вопрос — «а какой у
канала номер?». Ответить на него человеку неоткуда: в интерфейсе MAX номер
канала не показывается нигде, а метод API `GET /chats`, которым его брали
раньше, MAX отключил в июне 2026. Осталась ровно одна дорога: бот узнаёт номер
в тот момент, когда его ДОБАВЛЯЮТ в канал, — событием `bot_added`. Событие
приходит один раз и лежит в очереди обновлений несколько часов, поэтому команду
надо позвать вскоре после добавления бота.

ПОРЯДОК ДЛЯ ЧЕЛОВЕКА:
    1. завести бота в MAX через @MasterBot, забрать токен;
    2. положить токен в .env как MAX_BOT_TOKEN;
    3. добавить бота в свой канал администратором;
    4. запустить эту команду — она напечатает номер канала;
    5. записать номер в .env как MAX_CHAT_ID.

Запуск:
    python -m story_publisher.max_chats
    python -m story_publisher.max_chats --wait 60   # ждать событие до минуты

СЕТЬ ТОЛЬКО НА ЧТЕНИЕ. Программа читает очередь обновлений и ничего не
публикует и не меняет.
"""
from __future__ import annotations

import argparse
import sys
import time

from .config import config_for
from .drivers.max import API_DEFAULT

#: События, из которых можно достать номер чата. `bot_added` — бота добавили в
#: канал (наш случай), `bot_started` — человек написал боту в личку, `message_*`
#: — любое сообщение в чате, где бот состоит. Берём всё: если событие о
#: добавлении уже утекло из очереди, номер всё равно найдётся в любом другом.
_ЛЮБОЕ_СОБЫТИЕ_С_ЧАТОМ = ("bot_added", "bot_started", "message_created",
                          "message_callback", "chat_title_changed")


def _чаты_из_обновлений(updates):
    """{chat_id: описание} из списка обновлений. Порядок не гарантирован."""
    найдено = {}
    for upd in updates or []:
        cid = upd.get("chat_id")
        if cid is None:
            cid = ((upd.get("message") or {}).get("recipient") or {}).get("chat_id")
        if cid is None:
            continue
        тип = upd.get("update_type") or "?"
        кто = upd.get("user") or {}
        подпись = найдено.get(cid) or []
        метка = "%s%s" % (тип, (" от @%s" % кто["username"]) if кто.get("username") else "")
        if метка not in подпись:
            подпись.append(метка)
        найдено[cid] = подпись
    return {cid: ", ".join(меток) for cid, меток in найдено.items()}


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Показать номера каналов/чатов MAX, известные боту.")
    ap.add_argument("--wait", type=int, default=0, metavar="СЕК",
                    help="ждать появления события до СЕК секунд (по умолчанию "
                         "не ждать — прочитать очередь и выйти)")
    a = ap.parse_args(argv)

    cfg = config_for()
    token = cfg.secret("max_bot_token")
    if not token:
        print("Токена бота MAX нет. Сначала заведите бота в MAX через "
              "@MasterBot и положите токен в .env как MAX_BOT_TOKEN.")
        return 2

    base = (cfg.any_value("max_api_base") or API_DEFAULT).rstrip("/")

    try:
        import requests
    except ImportError:
        print("Нет пакета requests — установите зависимости модуля.")
        return 2

    дедлайн = time.time() + max(0, a.wait)
    while True:
        try:
            r = requests.get("%s/updates" % base,
                             headers={"Authorization": token},
                             params={"limit": 100}, timeout=40)
        except requests.RequestException as err:
            print("MAX недоступен: %s" % err)
            return 2

        if r.status_code >= 400:
            print("MAX отказал: HTTP %s. Проверьте токен бота и адрес API "
                  "(max_api_base)." % r.status_code)
            return 2
        try:
            body = r.json()
        except ValueError:
            print("MAX ответил не-JSON: HTTP %s" % r.status_code)
            return 2

        чаты = _чаты_из_обновлений(body.get("updates"))
        if чаты:
            print("Номера чатов и каналов, которые бот уже видел:\n")
            for cid, откуда in чаты.items():
                print("  %s   (из событий: %s)" % (cid, откуда))
            print("\nНужный канал — тот, куда вы добавили бота администратором.")
            print("Запишите его номер в .env как MAX_CHAT_ID, и сторис пойдут "
                  "в MAX автоматически.")
            return 0

        if time.time() >= дедлайн:
            break
        time.sleep(3)

    print("Бот пока не видел ни одного чата — событий в очереди нет.")
    print("Это нормально, если бота ещё не добавили в канал. Порядок такой:")
    print("  1. откройте свой канал в MAX;")
    print("  2. добавьте туда бота АДМИНИСТРАТОРОМ (обычным участником он "
              "публиковать не сможет);")
    print("  3. запустите эту команду снова — номер появится.")
    print("\nЕсли бота добавили давно, событие могло уйти из очереди: напишите "
          "любое сообщение в канал и повторите команду.")
    return 2


if __name__ == "__main__":
    sys.exit(run())
