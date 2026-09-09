# -*- coding: utf-8 -*-
"""Драйвер Instagram — сторис через SmmBox.

ЧТО ИЗМЕНИЛОСЬ 04.09.2026 И ПОЧЕМУ. Раньше сторис Instagram уходили через
сторонний сервис публикации. Решением владельца из заводов остаются только два
внешних пути: SmmBox и прямой Telegram Bot API. Instagram покрывается SmmBox
(код площадки `io`), поэтому драйвер переведён на него.

🔴 ЧЕСТНО ПРО НОВЫЙ ПУТЬ. Режим сторис у SmmBox ЖИВЬЁМ НЕ ПОДТВЕРЖДЁН. В
`smmbox_post.build_options` записано: «Оба задания сервис ПРИНЯЛ с номером и
датой. Отказ выглядит как успех — читать посты у SmmBox нечем (шесть методов,
чтения нет)». Там же случай, когда появившаяся через три минуты история
оказалась ЧУЖОЙ, и поймали это только тем, что скачали картинку и посмотрели.

Поэтому успешный ответ сервиса возвращается статусом `unconfirmed`, а не
`accepted`: «задание принято» и «история вышла» — разные события, и второе мы
утверждать не вправе, пока не будет живого прогона с проверкой глазами.
Вся отправка живёт в общем слое `smmbox_story` — своего HTTP здесь нет.

ОГРАНИЧЕНИЕ ССЫЛКИ сохраняется и не зависит от сервиса: ссылок и стикеров в
сторис через API не ставит никто. Задана ссылка — драйвер НЕ публикует, а
возвращает `requires_manual` и кладёт памятку, чтобы человек добавил ссылку
руками в приложении.
"""
from __future__ import annotations

from ..reminders import write_reminder
from . import smmbox_story

PLATFORM = "instagram"
ALIASES = ("ig", "insta")

#: Код площадки в SmmBox и тип адресата. Instagram у сервиса один — бизнес- или
#: личный профиль подключается в его кабинете, и для нас это один и тот же `io`.
SOCIAL = "io"
TARGET_TYPE = "user"


def publish(media_path, caption, link, cfg, dry_run):
    # 1) Ссылка → ручной режим, вне зависимости от dry_run и от сервиса.
    if link:
        reason = ("в сторис Instagram ссылки и стикеры через API не ставит ни "
                  "один сервис — их добавляют в приложении вручную")
        rem = write_reminder(PLATFORM, media_path, caption, link,
                             client_id=cfg.client_id, reason=reason)
        return {
            "status": "requires_manual",
            "platform": PLATFORM,
            "media": str(media_path),
            "dry_run": bool(dry_run),
            "reminder": str(rem),
            "detail": ("Сторис со ссылкой не публикуется автоматически. "
                       "Памятка сохранена: %s" % rem),
        }

    # 2) Всё остальное — общий слой SmmBox.
    intent = smmbox_story.publish_story(
        media_path, caption, cfg, dry_run,
        social=SOCIAL, gtype=TARGET_TYPE,
        group_id=cfg.value("smmbox_instagram_group_id"))
    intent["platform"] = PLATFORM
    return intent
