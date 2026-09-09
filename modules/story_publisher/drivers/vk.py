# -*- coding: utf-8 -*-
"""Драйвер ВКонтакте — сторис (stories.*).

СОСТОЯНИЕ. Сторис ВК — отдельный API, не wall.*:
  stories.getPhotoUploadServer / stories.getVideoUploadServer  → upload_url
  → POST файла на upload_url (поле "file") → stories.save(upload_results=...)
Нужен ПОЛЬЗОВАТЕЛЬСКИЙ токен формата vk1.a. (Kate Mobile, тот же, что у
vkcarousel.py/vkpost.py). Токен vk2.a. (VK ID / PKCE) методы не принимает
(ошибка 1051), токен сообщества сторис не умеет. Ходим голым requests через
api.vk.com/method — НЕ тянем пакет vk-api (тот же слой, что у завода).

КЛЮЧ ОДИН НА ВЕСЬ ВК. Канон — `VK_USER_TOKEN_KATE` (его же читают vkcarousel.py
и vkpost.py); `vk_stories_token` — прежнее имя того же ключа, оставлено алиасом
ради уже установленных копий. Таблица имён одна: ALIASES в
modules/publishing/config_reader.py. Отдельного «токена для сторис» не бывает.
Получается ключ командой `python vk_auth.py --kate` (в сеть не ходит).

⚠️ ЖИВЬЁМ НЕ ПРОВЕРЕНО. Ни один шаг ниже ни разу не проходил по-настоящему:
поток собран по документации ВК. Не подтверждены две вещи — даёт ли конкретный
аккаунт право публиковать истории через API и то ли имя у поля загрузки ("file").
Поэтому пустой ответ ВК объявляется «unconfirmed», а не «опубликовано», и doctor
прямо говорит клиенту, что первый ВК-прогон надо посмотреть глазами. Не считать
проверенным, пока не отработает живьём.

ССЫЛКА В СТОРИС ВК возможна (link_text/link_url), но доступна не всем аккаунтам —
помечаем как «требует проверки прав», не переоцениваем.

Предохранитель: боевая отправка только при dry_run=False.
"""
from __future__ import annotations

import os

from . import smmbox_story

PLATFORM = "vk"
ALIASES = ("vkontakte", "vk_stories")

API = "https://api.vk.com/method/"
API_VERSION = "5.199"

_VIDEO_EXT = (".mp4", ".mov")


def _token(cfg):
    # any_value — ровно те же ключи, в том же порядке и из тех же источников,
    # что проверяет doctor. Разойтись им нельзя: расхождение опаснее самой
    # нехватки ключа (доктор зелёный — отправка падает, или наоборот).
    # Канон — vk_user_token_kate; vk_stories_token остался алиасом.
    return cfg.any_value("vk_user_token_kate", "vk_stories_token")


def _is_video(media_path):
    return os.path.splitext(str(media_path))[1].lower() in _VIDEO_EXT


def publish(media_path, caption, link, cfg, dry_run):
    intent = {
        "status": None,
        "platform": PLATFORM,
        "media": str(media_path),
        "dry_run": bool(dry_run),
    }

    # ── КУДА: личная страница или сообщество ─────────────────────────────────
    # Это ДВА РАЗНЫХ пути, и путать их нельзя.
    #
    # Личная страница — прямой `stories.*` пользовательским токеном. Работает,
    # подтверждено владельцем живьём (VK-11 снят 01.09.2026), и остаётся здесь
    # ровно поэтому: менять проверенное на непроверенное незачем.
    #
    # Сообщество — прямым API НЕВОЗМОЖНО: токен сообщества сторис не умеет
    # (см. шапку файла). Единственный путь — SmmBox (`vk`/`group`).
    #
    # ⚠️ И ещё одно, чтобы не путали дальше: «на личную страницу ВК нельзя»
    # относилось к СТЕНЕ (wall.post — прав `wall`/`photos` ВК новым приложениям
    # не выдаёт, docs/SMMBOX.md). К СТОРИС это никогда не относилось.
    куда = (cfg.value("vk_story_target") or "user").strip().lower()
    if куда in ("group", "club", "сообщество", "группа"):
        intent = smmbox_story.publish_story(
            media_path, caption, cfg, dry_run,
            social="vk", gtype="group",
            group_id=cfg.value("smmbox_vk_group_id"))
        intent["platform"] = PLATFORM
        return intent

    # 🔴 ЗАПРЕТ ОТ 08.09.2026. Ниже начинался путь stories.* на ключе vk1.a.,
    # который добывался прямым входом через чужое мобильное приложение. После
    # такого входа ВКонтакте блокирует аккаунт клиента целиком, и снять
    # блокировку может только сам владелец аккаунта. Запрещён не способ добыть
    # ключ, а ЛЮБАЯ работа им — поэтому отказ стоит ДО чтения ключа, и наличие
    # старого ключа в настройках ничего не меняет.
    #
    # Ветка «в сообщество» выше — она идёт через SmmBox и запретом не задета.
    intent["status"] = "not_configured"
    intent["missing"] = []
    intent["limitation"] = ("истории на личную страницу ВК закрыты запретом "
                            "от 08.09.2026")
    intent["detail"] = ("Истории на ЛИЧНУЮ страницу ВКонтакте не выходят: этот "
                        "путь работал прямым входом в ВК, а после него ВК "
                        "блокирует аккаунт целиком — снять блокировку может "
                        "только сам владелец аккаунта. Ключ в настройках, если "
                        "он есть, не используется и удалять его не нужно. "
                        "Истории в СООБЩЕСТВО ВК работают через SmmBox — "
                        "просите их словами «выложи историю в сообщество».")
    return intent

    is_video = _is_video(media_path)
    get_method = ("stories.getVideoUploadServer" if is_video
                  else "stories.getPhotoUploadServer")
    intent["kind"] = "video" if is_video else "photo"

    if link:
        intent["limitation"] = ("ссылка в сторис ВК доступна не всем аккаунтам — "
                                "требует проверки прав аккаунта")

    if dry_run:
        intent["status"] = "dry_run"
        intent["flow"] = ["%s(add_to_news=1)" % get_method,
                          "POST файла на upload_url (поле file)",
                          "stories.save(upload_results=...)"]
        intent["detail"] = ("Готово к отправке сторис в ВК (%s). Пробный прогон — "
                            "ничего не отправлено. Нужен рабочий токен vk1.a."
                            % intent["kind"])
        return intent

    return _send(token, media_path, get_method, link, intent)


def _api(token, method, **params):  # pragma: no cover - боевой путь
    """POST api.vk.com/method/<method> — тот же слой, что call() у завода."""
    import requests

    params.update({"access_token": token, "v": API_VERSION})
    resp = requests.post(API + method, data=params, timeout=180)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        raise RuntimeError("VK %s: [%s] %s" % (method,
                           err.get("error_code"), err.get("error_msg")))
    return data.get("response", {})


def _send(token, media_path, get_method, link, intent):  # pragma: no cover - боевой путь
    import requests

    try:
        # 1) upload_url. add_to_news=1 — показать в ленте (как у завода).
        params = {"add_to_news": 1}
        if link:
            # link_text — из допустимого списка ВК; link_address — URL.
            params["link_text"] = "open"
            params["link_address"] = link
        server = _api(token, get_method, **params)
        upload_url = server.get("upload_url")
        if not upload_url:
            intent["status"] = "error"
            intent["detail"] = "ВК не вернул upload_url: %s" % server
            return intent

        # 2) POST файла на upload_url (поле "file").
        with open(str(media_path), "rb") as fh:
            up = requests.post(
                upload_url,
                files={"file": (os.path.basename(str(media_path)), fh)},
                timeout=600,
            ).json()
        upload_result = up.get("response", {}).get("upload_result") or up.get("upload_result")
        if not upload_result:
            intent["status"] = "error"
            intent["detail"] = "Сервер ВК не вернул upload_result: %s" % up
            return intent

        # 3) stories.save.
        saved = _api(token, "stories.save", upload_results=upload_result)
        items = saved.get("items", []) if isinstance(saved, dict) else []
        intent["saved"] = len(items)
        if not items:
            # ПУСТОЙ items — НЕ УСПЕХ. ВК ответил без ошибки, но ни одной
            # истории не назвал: подтвердить публикацию нечем. Раньше здесь
            # стояло безусловное «accepted» и клиент читал «Сторис ВК
            # опубликована (сохранено объектов: 0)» — фраза, которая сама себя
            # опровергает. Отказом это тоже звать нельзя: материал мог уйти.
            # Тот же разбор, что у instagram.py/_send_video (статус unconfirmed).
            intent["status"] = "unconfirmed"
            intent["detail"] = ("ВК принял запрос, но ни одной истории в ответе "
                                "не назвал — подтвердить публикацию нечем. "
                                "Посмотрите сторис в ВК глазами; если её нет — "
                                "выложите вручную с телефона.")
            return intent
        intent["status"] = "accepted"
        intent["detail"] = "Сторис ВК опубликована (сохранено объектов: %d)." % len(items)
        return intent
    except (RuntimeError, ValueError, OSError,
            requests.RequestException) as ex:  # noqa: PERF203
        intent["status"] = "error"
        intent["detail"] = "Сторис ВК не отправлена: %s" % ex
        return intent
