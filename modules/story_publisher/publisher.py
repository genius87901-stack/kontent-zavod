# -*- coding: utf-8 -*-
"""Единый адаптер публикации сторис: publish_story(...).

Одна функция на все площадки. Выбирает драйвер (ig/tg/vk), валидирует вход и
логирует НАМЕРЕНИЕ. По умолчанию dry_run=True — реальной отправки нет.

Мультитенантность: client_id выбирает секцию конфига клиента; без него —
клиент по умолчанию (одноклиентный режим).

СЕРИЯ (28.08.2026). `media_path` принимает ОДИН путь (как раньше, поведение
байт-в-байт) ИЛИ СПИСОК путей. Список — это «карусель» в сторис: листаемой
карусели внутри одной истории у площадок нет, её штатный эквивалент — несколько
кадров подряд. Кадры уходят строго по порядку, каждый отдельной историей.
"""
from __future__ import annotations

import logging

from .config import config_for
from .drivers import get_driver, known_platforms
from .paths import resolve_media

log = logging.getLogger("story_publisher")

#: Статус, при котором кадр ДЕЙСТВИТЕЛЬНО ушёл на площадку. Тот же список, что
#: у cli._SENT_STATUSES — разойтись им нельзя.
_SENT = ("accepted",)


def publish_story(platform, media_path, caption, link=None, client_id=None,
                  dry_run=True):
    """Опубликовать (или в dry_run — подготовить) сторис.

    platform   — ig / tg / vk (или полные имена / псевдонимы).
    media_path — путь к готовому медиа (абсолютный или относительно корня
                 установки) ЛИБО список путей — тогда это серия историй подряд.
                 Рендер здесь НЕ делается — только публикация. Кадр может быть
                 и картинкой, и вертикальным видео (готовый reels.mp4): драйверы
                 всех трёх площадок различают их по расширению сами.
    caption    — текст (может быть пустым).
    link       — ссылка. Для Instagram включает ручной режим (Upload-Post ссылок
                 в сторис не умеет).
    client_id  — неймспейс конфига клиента; None → клиент по умолчанию.
    dry_run    — True (по умолчанию): не отправлять, только валидировать и
                 залогировать намерение.

    Возвращает dict со status и человекочитаемым detail. Для серии — тот же
    dict плюс `series` (результат по каждому кадру) и `count`. Исключений наружу
    не бросает при штатных ошибках ввода — возвращает status="error".
    """
    if isinstance(media_path, (list, tuple)):
        return _publish_series(platform, list(media_path), caption, link,
                               client_id, dry_run)
    return _publish_one(platform, media_path, caption, link, client_id, dry_run)


def _publish_one(platform, media_path, caption, link, client_id, dry_run):
    """Одна история. Путь, который был здесь до появления серий, — без изменений."""
    who = client_id or "default"

    driver = get_driver(platform)
    if driver is None:
        detail = ("Неизвестная площадка: %r. Доступны: %s"
                  % (platform, ", ".join(known_platforms())))
        log.warning(detail)
        return {"status": "error", "platform": platform, "client_id": who,
                "dry_run": bool(dry_run), "detail": detail}

    media = resolve_media(media_path)
    if not media.exists() or not media.is_file():
        detail = "Файл медиа не найден: %s" % media
        log.warning(detail)
        return {"status": "error", "platform": driver.PLATFORM, "client_id": who,
                "dry_run": bool(dry_run), "detail": detail}

    cfg = config_for(client_id)

    # Лог намерения БЕЗ секретов: только факт, площадка, клиент, наличие ссылки.
    log.info("НАМЕРЕНИЕ: площадка=%s клиент=%s медиа=%s ссылка=%s dry_run=%s",
             driver.PLATFORM, who, media.name, bool(link), bool(dry_run))

    result = driver.publish(media, caption, link, cfg, dry_run)
    result.setdefault("client_id", who)
    result.setdefault("platform", driver.PLATFORM)
    result.setdefault("dry_run", bool(dry_run))

    log.info("РЕЗУЛЬТАТ: площадка=%s клиент=%s статус=%s",
             result.get("platform"), who, result.get("status"))
    return result


def _publish_series(platform, paths, caption, link, client_id, dry_run):
    """Серия историй подряд.

    ОСТАНОВКА НА ПЕРВОМ ОТКАЗЕ (боевой прогон). Опубликованную историю отозвать
    нечем: если третий кадр не ушёл, продолжать четвёртым — значит выложить
    серию С ДЫРОЙ ПОСЕРЕДИНЕ, а это хуже оборванной серии. Останавливаемся, и
    вслух говорим, сколько кадров ушло и на каком встали. В пробном прогоне не
    останавливаемся никогда: там показать надо ВЕСЬ план целиком.

    ПОДПИСЬ — ТОЛЬКО ПЕРВОМУ КАДРУ. Одна и та же подпись на восьми историях
    подряд читается как ошибка завода, а не как замысел.
    """
    who = client_id or "default"
    if not paths:
        return {"status": "error", "platform": platform, "client_id": who,
                "dry_run": bool(dry_run),
                "detail": "Серия сторис пуста — не передано ни одного кадра."}

    results, sent = [], 0
    stopped_at = None
    for i, one in enumerate(paths, 1):
        res = _publish_one(platform, one, caption if i == 1 else "",
                           link if i == 1 else None, client_id, dry_run)
        res["frame"] = i
        results.append(res)
        if res.get("status") in _SENT:
            sent += 1
        elif not dry_run:
            stopped_at = i
            break

    total = len(paths)
    out = {"platform": (results[0].get("platform") or platform),
           "client_id": who, "dry_run": bool(dry_run),
           "series": results, "count": total, "sent": sent}

    if dry_run:
        # Сводная строка не имеет права быть бодрее кадров. Если НИ ОДИН кадр не
        # дошёл даже до состояния «готов к отправке» (площадка не подключена,
        # файла нет, ссылка требует рук), то «пробный прогон готов» — враньё:
        # с --send отсюда не уйдёт ничего. Показываем настоящую причину.
        statuses = {r.get("status") for r in results}
        if "dry_run" not in statuses:
            only = statuses.pop() if len(statuses) == 1 else "error"
            out["status"] = only
            out["detail"] = ("Серия из %d историй К ОТПРАВКЕ НЕ ГОТОВА. Причина "
                             "на каждом кадре: %s"
                             % (total, results[0].get("detail") or only))
            return out
        out["status"] = "dry_run"
        out["detail"] = ("Серия из %d историй готова к отправке. Пробный прогон — "
                         "ничего не отправлено." % total)
        if len(statuses) > 1:
            out["detail"] += (" ВНИМАНИЕ: не все кадры готовы — смотрите разбор "
                              "по кадрам ниже.")
        return out

    if sent == total:
        out["status"] = "accepted"
        out["detail"] = "Серия опубликована целиком: %d историй подряд." % total
    elif sent == 0:
        # Ни один кадр не ушёл — причина у всех одна и та же, показываем её.
        first = results[0]
        out["status"] = first.get("status") or "error"
        out["detail"] = ("Серия НЕ опубликована (0 из %d). Причина на первом же "
                         "кадре: %s" % (total, first.get("detail") or out["status"]))
    else:
        out["status"] = "partial"
        out["detail"] = (
            "СЕРИЯ ОБОРВАНА: ушло %d историй из %d, дальше встали на кадре %s. "
            "Опубликованное отозвать нечем — досмотрите серию глазами и выложите "
            "остаток вручную с телефона (или удалите выложенное и повторите "
            "целиком)." % (sent, total, stopped_at))
    log.info("РЕЗУЛЬТАТ СЕРИИ: площадка=%s клиент=%s ушло=%d из %d статус=%s",
             out["platform"], who, sent, total, out["status"])
    return out
