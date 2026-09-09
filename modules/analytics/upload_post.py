# -*- coding: utf-8 -*-
"""Тонкий клиент Upload-Post для ЖИВОГО сбора аналитики (BOX-19).

Это ТОТ ЖЕ сервис, что публикует контент (`autopost.py` / модуль `publishing`) —
у него же есть и аналитика вышедших постов. Клиент на чистом stdlib (`urllib`),
чтобы модуль `analytics` остался без сторонних зависимостей (manifest `pip: []`).

БЕЗОПАСНОСТЬ (предохранитель платного сервиса, CONTRACT.md §3):
  * Ключ читается КАК В publishing: окружение → `.env` корня установки. Никогда из
    `factory.config`, никогда в код/лог. `read_key()` отдаёт значение или None;
    сам ключ НЕ печатается и НЕ попадает в тексты ошибок (в URL его тоже нет — он
    только в заголовках авторизации).
  * Аутентифицированные (платные) вызовы делаются ТОЛЬКО при наличии ключа. Нет
    ключа → `UploadPostAuthMissing`; наверху это ловится как «урезанный режим»,
    чужой аккаунт не жжём.
  * Ни один вызов отсюда ничего НЕ публикует: только GET по эндпоинтам чтения.

РЕАЛЬНАЯ РАСКЛАДКА ЭНДПОИНТОВ (сверено по openapi.json docs.upload-post.com,
август 2026). База: `https://api.upload-post.com/api`.

  * `GET /analytics/{profile_username}?platforms=…` — ЖИВЫЕ метрики профиля и его
    постов. Идёт в сами площадки, поэтому лимит 100 запросов / 5 минут.
  * `GET /uploadposts/post-analytics/cached?user=…` — снимки метрик постов из
    кэша сервиса. Площадки при этом НЕ дёргаются → лимита 100/5мин НЕТ. Это наш
    ОСНОВНОЙ путь: дёшево, безопасно, отдаёт сразу пачку постов.
    Ответ: {source:"snapshot_cache", posts:[{post_id, platform, captured_at,
    metrics{…}, post_url, media_type, upload_timestamp}], next_cursor, has_more}.
  * `GET /uploadposts/status?request_id=…` — статус нашей публикации. Именно
    отсюда мы достаём НАТИВНЫЙ id/ссылку поста на площадке по сохранённому при
    публикации `request_id` (шов с `autopost.persist_published`).
  * `GET /uploadposts/history?page=&limit=` — журнал наших публикаций на стороне
    сервиса (запасной путь, если локального журнала нет).

ЧЕГО В СПЕКЕ НЕТ (важно, тут прошлая версия модуля ошибалась): эндпоинтов
`/uploadposts/post-analytics/{request_id}`, `/uploadposts/platform-metrics` и
`/uploadposts/total-impressions/{profile}` в текущей спецификации НЕТ. Связь
«наш request_id → метрики» строится в два шага: status → нативный id/ссылка →
поиск этого поста в cached-пачке (см. `live.py`).
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import install_root

BASE = "https://api.upload-post.com/api"
DEFAULT_TIMEOUT = 20          # секунд на один запрос
DEFAULT_RETRIES = 3           # всего попыток (1 обычная + 2 повтора)
BACKOFF_BASE = 1.5            # секунд, растёт степенью двойки + джиттер
BACKOFF_CAP = 20.0            # потолок паузы, чтобы не подвесить установку

# Внутренняя площадка → токен площадки в Upload-Post. VK сервис аналитикой (и
# публикацией) не покрывает — он идёт отдельным путём, поэтому None: честно «нет
# живого источника Upload-Post». Telegram Upload-Post публикует; в списке
# аналитики спеки его нет — деградируем на образец, если метрик не пришло.
PLATFORM_MAP = {
    "instagram": "instagram",
    "telegram": "telegram",
    "vk": None,
}

# Площадки, по которым сервис ЗАЯВЛЯЕТ аналитику (спека, параметр `platforms`).
ANALYTICS_PLATFORMS = ("instagram", "tiktok", "linkedin", "facebook", "x",
                       "youtube", "threads", "pinterest", "reddit", "bluesky")


class UploadPostError(RuntimeError):
    """Сбой обращения к Upload-Post (сеть / HTTP / битый ответ). Без секретов."""
    retryable = False


class UploadPostAuthMissing(UploadPostError):
    """Нет ключа UPLOAD_POST_API_KEY — аутентифицированный вызов не делаем."""


class UploadPostAuthRejected(UploadPostError):
    """Ключ есть, но сервис его не принял (401/403). Повторять бессмысленно."""


class UploadPostNotFound(UploadPostError):
    """404 — такого профиля/запроса у сервиса нет. Повторять бессмысленно."""


class UploadPostRateLimited(UploadPostError):
    """429 — упёрлись в лимит (у живой аналитики он 100 запросов / 5 минут)."""
    retryable = True


class UploadPostOffline(UploadPostError):
    """Сети нет / DNS / таймаут. Ровно этот случай = офлайн-установка клиента."""
    retryable = True


class UploadPostServerError(UploadPostError):
    """5xx на стороне сервиса — имеет смысл повторить с паузой."""
    retryable = True


class UploadPostBadResponse(UploadPostError):
    """Ответ пришёл, но это не JSON нужной формы."""


# --- точка подмены транспорта для ОФЛАЙН-ТЕСТОВ ------------------------------
# Тесты (и любой офлайн-прогон) подставляют сюда функцию
# `fn(path, params, key, timeout) -> dict`, и ни одного сетевого байта не летит.
# В боевом режиме здесь None и работает `_http_get`.
TRANSPORT = None


def set_transport(fn):
    """Подменить транспорт (для тестов на записанных ответах). None = вернуть
    боевой urllib. Ничего платного сам по себе не делает."""
    global TRANSPORT
    prev, TRANSPORT = TRANSPORT, fn
    return prev


# --- ключ/пользователь: env → .env корня установки (как publishing) ----------

def _env_file() -> Path:
    return install_root() / ".env"


def _load_env_file() -> dict:
    path = _env_file()
    if not path.exists():
        return {}
    out = {}
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def read_key() -> "str | None":
    """Ключ Upload-Post: окружение → `.env` корня установки. НЕ из factory.config,
    НЕ логируется. None, если ключа нет (урезанный режим)."""
    env = _load_env_file()
    val = (os.environ.get("UPLOAD_POST_API_KEY")
           or env.get("UPLOAD_POST_API_KEY") or "").strip()
    return val or None


def read_user() -> "str | None":
    """Идентификатор профиля Upload-Post (параметр `user`)."""
    env = _load_env_file()
    val = (os.environ.get("UPLOAD_POST_USER")
           or env.get("UPLOAD_POST_USER") or "").strip()
    return val or None


def has_key() -> bool:
    return read_key() is not None


def key_shape() -> str:
    """Безопасное описание ключа для doctor: длина и есть ли пробелы. САМ КЛЮЧ НЕ
    ВОЗВРАЩАЕТСЯ ни целиком, ни куском — только форма."""
    key = read_key()
    if not key:
        return "нет"
    bad = " " in key or "\t" in key
    return "есть, длина %d%s" % (len(key), ", ВНУТРИ ПРОБЕЛ — похоже на опечатку"
                                 if bad else "")


# --- низкоуровневый GET с ретраями и бэкоффом --------------------------------

def _http_get(path, params=None, key=None, timeout=DEFAULT_TIMEOUT):
    """Один боевой GET. Поднимает типизированные исключения. Ключ уходит ТОЛЬКО в
    заголовках (в URL его нет), в текст ошибки не попадает."""
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if key:
        # Боевая публикация (autopost.py) ходит с `Authorization: Apikey <ключ>`,
        # спека объявляет `X-API-Key`. Шлём оба — лишний заголовок сервис
        # игнорирует, а мы не зависим от того, какая из схем актуальна.
        req.add_header("Authorization", "Apikey %s" % key)
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        code = ex.code
        if code in (401, 403):
            raise UploadPostAuthRejected(
                "Upload-Post не принял ключ (HTTP %d на %s). Проверь "
                "UPLOAD_POST_API_KEY — он тот же, что для публикации."
                % (code, path)) from None
        if code == 404:
            raise UploadPostNotFound(
                "Upload-Post: не найдено (HTTP 404 на %s)" % path) from None
        if code == 429:
            raise UploadPostRateLimited(
                "Upload-Post: превышен лимит запросов (HTTP 429 на %s). У живой "
                "аналитики лимит 100 запросов / 5 минут." % path) from None
        if code >= 500:
            raise UploadPostServerError(
                "Upload-Post: сбой на стороне сервиса (HTTP %d на %s)"
                % (code, path)) from None
        raise UploadPostError("Upload-Post ответил HTTP %d на %s"
                              % (code, path)) from None
    except (urllib.error.URLError, OSError) as ex:
        reason = getattr(ex, "reason", ex)
        raise UploadPostOffline("нет связи с Upload-Post (%s): %s"
                                % (path, reason)) from None
    try:
        data = json.loads(body)
    except ValueError:
        raise UploadPostBadResponse("Upload-Post вернул не-JSON на %s" % path) \
            from None
    if not isinstance(data, dict):
        raise UploadPostBadResponse("Upload-Post вернул не объект на %s" % path)
    return data


def _sleep(seconds):
    """Вынесено отдельной функцией, чтобы тесты не ждали по-настоящему."""
    time.sleep(seconds)


def _request(path, params=None, key=None, timeout=DEFAULT_TIMEOUT,
             retries=DEFAULT_RETRIES):
    """GET с повторами и экспоненциальным бэкоффом.

    Повторяем ТОЛЬКО то, что имеет шанс починиться само: сеть/таймаут (offline),
    429 (лимит), 5xx. Отказ ключа (401/403), 404 и битый JSON НЕ повторяем — это
    не чинится ожиданием, а лишние повторы жгут лимит чужого аккаунта.

    Пауза: BACKOFF_BASE * 2^n + случайный джиттер, но не больше BACKOFF_CAP —
    иначе установка на минуту «повиснет» без объяснений."""
    if TRANSPORT is not None:                      # офлайн-тесты
        return TRANSPORT(path, params, key, timeout)
    attempts = max(1, int(retries or 1))
    last = None
    for n in range(attempts):
        try:
            return _http_get(path, params=params, key=key, timeout=timeout)
        except UploadPostError as ex:
            last = ex
            if not getattr(ex, "retryable", False) or n == attempts - 1:
                raise
            pause = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** n))
            _sleep(pause + random.uniform(0, 0.4 * pause))
    raise last                                     # недостижимо, но пусть будет


# --- ЧТЕНИЕ (все требуют ключ; ничего не публикуют) --------------------------

def _need_key(key):
    key = key or read_key()
    if not key:
        raise UploadPostAuthMissing(
            "нет ключа UPLOAD_POST_API_KEY — живой сбор Upload-Post недоступен "
            "(ключ кладётся как для публикации: окружение или .env)")
    return key


def upload_status(request_id, key=None, timeout=DEFAULT_TIMEOUT,
                  retries=DEFAULT_RETRIES) -> dict:
    """GET /uploadposts/status?request_id=… — чем закончилась НАША публикация.
    Отсюда достаём нативный id/ссылку поста на площадке (см. `post_refs`)."""
    return _request("/uploadposts/status", params={"request_id": request_id},
                    key=_need_key(key), timeout=timeout, retries=retries)


def cached_post_analytics(user=None, platform=None, limit=None, cursor=None,
                          since=None, until=None, key=None,
                          timeout=DEFAULT_TIMEOUT,
                          retries=DEFAULT_RETRIES) -> dict:
    """GET /uploadposts/post-analytics/cached — снимки метрик постов ИЗ КЭША
    сервиса. Площадки не дёргаются → лимита 100/5мин нет. Основной путь модуля.
    `limit` по умолчанию у сервиса 50, максимум 200. Даты — YYYY-MM-DD."""
    if limit is not None:
        limit = max(1, min(200, int(limit)))
    return _request("/uploadposts/post-analytics/cached",
                    params={"user": user or read_user(), "platform": platform,
                            "limit": limit, "cursor": cursor,
                            "since": since, "until": until},
                    key=_need_key(key), timeout=timeout, retries=retries)


def profile_analytics(profile_username, platforms=None, page_id=None,
                      page_urn=None, key=None, timeout=DEFAULT_TIMEOUT,
                      retries=DEFAULT_RETRIES) -> dict:
    """GET /analytics/{profile_username}?platforms=… — ЖИВЫЕ агрегаты профиля.
    Дёргает сами площадки → попадает под лимит 100 запросов / 5 минут, поэтому
    зовём редко и только явно (cli profile), а не в общем ingest."""
    if isinstance(platforms, (list, tuple, set)):
        platforms = ",".join(sorted(str(p) for p in platforms if p))
    return _request("/analytics/%s"
                    % urllib.parse.quote(str(profile_username), safe=""),
                    params={"platforms": platforms, "page_id": page_id,
                            "page_urn": page_urn},
                    key=_need_key(key), timeout=timeout, retries=retries)


def upload_history(page=1, limit=20, key=None, timeout=DEFAULT_TIMEOUT,
                   retries=DEFAULT_RETRIES) -> dict:
    """GET /uploadposts/history — журнал наших публикаций на стороне сервиса.
    Запасной путь, когда локального `.state/published_ids.jsonl` нет."""
    return _request("/uploadposts/history",
                    params={"page": page, "limit": limit},
                    key=_need_key(key), timeout=timeout, retries=retries)


# --- разбор ответов (ЧИСТЫЕ функции, без сети — их и покрывают тесты) --------

def _as_list(val):
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return [val]
    return []


def cached_posts(raw) -> list:
    """Из ответа cached достаёт список снимков постов. Терпимо к форме: список
    бывает под `posts`, `data`, `results` или прямо корнем. Мусор → []."""
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if not isinstance(raw, dict):
        return []
    for keyname in ("posts", "data", "results", "items"):
        val = raw.get(keyname)
        if isinstance(val, list):
            return [p for p in val if isinstance(p, dict)]
    return []


def next_cursor(raw):
    """Токен следующей страницы cached-ответа (None = страниц больше нет)."""
    if not isinstance(raw, dict):
        return None
    if raw.get("has_more") is False:
        return None
    cur = raw.get("next_cursor") or raw.get("cursor")
    return cur or None


def snapshot_metrics(snapshot) -> dict:
    """Плоский блок метрик из одного снимка cached. Метрики лежат под `metrics`,
    но бывает и плоско — тогда отбрасываем служебные поля."""
    if not isinstance(snapshot, dict):
        return {}
    metrics = snapshot.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    service = {"post_id", "platform", "captured_at", "post_url", "media_type",
               "upload_timestamp", "user", "request_id"}
    return {k: v for k, v in snapshot.items() if k not in service}


def snapshot_keys(snapshot) -> set:
    """Все опознавательные знаки снимка: нативный id и ссылка (нормализованные).
    По ним мы связываем наш пост с записью Upload-Post."""
    out = set()
    if not isinstance(snapshot, dict):
        return out
    for keyname in ("post_id", "platform_post_id", "id", "media_id"):
        val = snapshot.get(keyname)
        if val:
            out.add(str(val).strip())
    for keyname in ("post_url", "url", "permalink", "link"):
        val = snapshot.get(keyname)
        if val:
            out.add(normalize_url(str(val)))
    return {k for k in out if k}


def normalize_url(url) -> str:
    """Ссылку приводим к сравнимому виду: без схемы, www, хвостового слэша,
    query и якоря. IG отдаёт ссылку то с `?igshid=…`, то без — иначе не сойдётся."""
    if not url:
        return ""
    s = str(url).strip().lower()
    s = s.split("#", 1)[0].split("?", 1)[0]
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def post_refs(status_raw, platform=None) -> set:
    """Из ответа /uploadposts/status достаёт опознавательные знаки вышедшего
    поста: нативный id и ссылку (по нужной площадке, если указана).

    Форма `result` в спеке — свободный объект (`additionalProperties: true`), в
    жизни это либо {площадка: {...}}, либо плоский блок, либо список. Поэтому
    обходим рекурсивно и собираем всё, что похоже на id/ссылку поста."""
    refs = set()
    if not isinstance(status_raw, dict):
        return refs
    plat = str(platform or "").lower()
    result = status_raw.get("result")
    if isinstance(result, dict) and plat and isinstance(result.get(plat), (dict, list)):
        scope = result.get(plat)
    else:
        scope = result if result is not None else status_raw

    id_keys = ("post_id", "platform_post_id", "id", "media_id", "video_id",
               "message_id")
    url_keys = ("post_url", "url", "permalink", "link", "share_url")

    def walk(node, depth=0):
        if depth > 5:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                lk = str(k).lower()
                if lk in id_keys and isinstance(v, (str, int)) and str(v).strip():
                    refs.add(str(v).strip())
                elif lk in url_keys and isinstance(v, str) and v.strip():
                    refs.add(normalize_url(v))
                elif isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(scope)
    return {r for r in refs if r}


def status_state(status_raw) -> str:
    """`processing` / `completed` / `failed` / `unknown`. Пока не completed —
    метрик ещё нет, и это не ошибка, а «рано»."""
    if not isinstance(status_raw, dict):
        return "unknown"
    val = status_raw.get("status") or status_raw.get("state")
    val = str(val or "").strip().lower()
    return val if val in ("processing", "completed", "failed") else "unknown"


def platform_block(raw, platform):
    """Из ответа профильной аналитики достаёт ПЛОСКИЙ блок метрик площадки.
    Ответ бывает вложенным (`results`/`analytics`/`data`/`metrics` → {platform:{…}})
    или уже плоским. Возвращает dict (возможно пустой)."""
    if not isinstance(raw, dict):
        return {}
    plat = str(platform or "").lower()
    for container in ("results", "analytics", "data", "metrics"):
        block = raw.get(container)
        if isinstance(block, dict):
            if plat and isinstance(block.get(plat), dict):
                return block[plat]
            if any(isinstance(v, (int, float)) for v in block.values()):
                return block
    if plat and isinstance(raw.get(plat), dict):
        return raw[plat]
    return raw


def impressions_field_map(pm):
    """{площадка: имя ГЛАВНОГО поля показов}. Форма ответа терпимая: корень бывает
    `{platforms:{…}}` или прямой. Используется картой полей из локальной фикстуры
    `fixtures/platform_metrics.sample.json` — живого эндпоинта под это в текущей
    спеке НЕТ, поэтому карта у нас локальная и правится руками."""
    out = {}
    if not isinstance(pm, dict):
        return out
    root = pm.get("platforms") if isinstance(pm.get("platforms"), dict) else pm
    if not isinstance(root, dict):
        return out
    for plat, info in root.items():
        if not isinstance(info, dict):
            continue
        for keyname in ("impressions_field", "main_metric", "primary_metric",
                        "impressions_metric", "views_field", "main_field",
                        "primary_field"):
            val = info.get(keyname)
            if isinstance(val, str) and val:
                out[str(plat).lower()] = val
                break
    return out
