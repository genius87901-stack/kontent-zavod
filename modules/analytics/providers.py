# -*- coding: utf-8 -*-
"""Абстракция ИСТОЧНИКА метрик: один провайдер на площадку (IG / TG / VK).

Ключевая мысль (как reels-путь content_plan сначала был заглушкой): вход-агностик.
Провайдер умеет ДВЕ вещи:
  * normalize(raw, field_map=None) — перевести «сырые» поля площадки/Upload-Post
    в единый нормализованный вид model.METRIC_FIELDS. Это ЧИСТАЯ функция, без сети.
    `field_map` (из публичного platform-metrics) подсказывает, какое поле у
    площадки считается ГЛАВНЫМ полем показов — оно у площадок разное;
  * fetch_live(post) — живой сбор через Upload-Post (тот же сервис, что публикует).
    По `request_id`/`platform_post_id`+`user` тянет метрики поста. БЕЗ ключа или
    для неподдерживаемой площадки — поднимает LiveSourceUnavailable (урезанный
    режим, платный аккаунт не трогаем), наверху ловится и деградирует на образец.

`fetch_metrics(post, live=False, field_map=None)` — единая точка входа: по площадке
выбирает провайдера и возвращает нормализованную запись метрик. По умолчанию
(live=False) берёт «сырьё» из самой карточки поста (образец-mock из
metrics.sample.json) и только нормализует — никакой сети. live=True идёт в
Upload-Post ТОЛЬКО при наличии ключа; иначе честно возвращает образец с пометкой.
"""
from __future__ import annotations

import json

from . import config, model, paths, upload_post
# Псевдоним, а не `from . import live`: у fetch_metrics параметр называется
# `live` (исторический публичный API) и затенил бы имя модуля.
from . import live as _live_module

# Единый тип «живого источника нет» живёт в live.py (там же вся оркестрация).
# Здесь переэкспорт ради обратной совместимости импортов.
LiveSourceUnavailable = _live_module.LiveSourceUnavailable


# Обратная совместимость: старое имя из прототипа-заглушки. Теперь это частный
# случай «живой источник недоступен» (например, площадка не покрыта Upload-Post).
class LiveSourceNotImplemented(LiveSourceUnavailable):
    """Устаревшее имя (BOX-15). Оставлено как подкласс LiveSourceUnavailable."""


def _pick(raw: dict, *keys):
    """Первое присутствующее из keys (сырьё площадки бывает в разных ключах)."""
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


class BaseProvider:
    platform = ""

    def normalize(self, raw: dict, field_map: dict = None) -> dict:
        raise NotImplementedError

    def _apply_field_map(self, m: dict, raw: dict, field_map: dict) -> dict:
        """Если площадка объявляет ГЛАВНОЕ поле показов (из platform-metrics) и мы
        ещё не заполнили показы/просмотры — берём значение оттуда. Так учитываем,
        что у IG это `impressions`/`reach`, у TikTok — `video_views`, у YouTube —
        `views` и т.д., не хардкодя это в каждом провайдере."""
        if not field_map or not isinstance(raw, dict):
            return m
        up_platform = upload_post.PLATFORM_MAP.get(self.platform) or self.platform
        field = field_map.get(str(up_platform).lower())
        if field and raw.get(field) is not None:
            if m.get("impressions") is None and m.get("views") is None:
                m["impressions"] = raw.get(field)
        return m

    def fetch_live(self, post: dict, session=None) -> dict:
        """Живой сбор через Upload-Post. Возвращает ПЛОСКИЙ блок «сырых» метрик
        площадки (его нормализует fetch_metrics). Вся оркестрация (ключ, ретраи,
        связь request_id→нативный id, кэш) — в `live.LiveSession`; провайдер лишь
        подставляет свою площадку. Нет ключа/ключа связи/сети → LiveSourceUnavailable.

        `session` переиспользуется между постами одного прогона: пачка снимков
        тянется один раз, а не по разу на пост."""
        session = session or _live_module.LiveSession()
        return session.fetch(post)["raw"]


class InstagramProvider(BaseProvider):
    """IG Graph Insights (media insights). Богатейший набор: охват, показы, плеи,
    среднее время просмотра, сохранения, шеры, переходы в профиль и по ссылке."""
    platform = "instagram"

    def normalize(self, raw: dict, field_map: dict = None) -> dict:
        m = model.empty_metrics()
        m["reach"] = _pick(raw, "reach", "accounts_reached")
        m["impressions"] = _pick(raw, "impressions", "impression_count")
        m["views"] = _pick(raw, "plays", "video_views", "views", "play_count",
                           "view_count")
        m["avg_watch_sec"] = _pick(raw, "avg_watch_time_sec",
                                   "ig_reels_avg_watch_time", "average_watch_time",
                                   "avg_watch_time")
        m["video_len_sec"] = _pick(raw, "video_length_sec", "duration_sec",
                                   "video_duration", "duration")
        m["likes"] = _pick(raw, "likes", "like_count")
        m["comments"] = _pick(raw, "comments", "comments_count", "comment_count")
        m["saves"] = _pick(raw, "saves", "saved", "save_count")
        m["shares"] = _pick(raw, "shares", "share_count", "reshares")
        m["profile_visits"] = _pick(raw, "profile_visits", "profile_activity",
                                    "profile_views")
        m["link_clicks"] = _pick(raw, "website_taps", "link_clicks",
                                 "website_clicks", "link_clicks_count")
        m["follows"] = _pick(raw, "follows", "follows_count")
        return self._apply_field_map(m, raw, field_map)


class TelegramProvider(BaseProvider):
    """TG-канал: у постов есть просмотры, реакции, пересылки (forwards), ответы,
    клики по ссылке. Охвата/сохранений в привычном IG-смысле нет → остаются None."""
    platform = "telegram"

    def normalize(self, raw: dict, field_map: dict = None) -> dict:
        m = model.empty_metrics()
        m["views"] = _pick(raw, "views", "view_count")
        m["forwards"] = _pick(raw, "forwards", "shares", "forward_count")
        m["comments"] = _pick(raw, "replies", "comments", "comment_count")
        # реакции TG кладём в likes (единый смысл «реакция одобрения»)
        m["likes"] = _pick(raw, "reactions", "likes", "reaction_count")
        m["link_clicks"] = _pick(raw, "link_clicks", "url_clicks")
        return self._apply_field_map(m, raw, field_map)


class VkProvider(BaseProvider):
    """VK-стена: просмотры, лайки, комментарии, репосты, клики по ссылке."""
    platform = "vk"

    def normalize(self, raw: dict, field_map: dict = None) -> dict:
        m = model.empty_metrics()
        m["views"] = _pick(raw, "views", "view_count")
        m["likes"] = _pick(raw, "likes", "like_count")
        m["comments"] = _pick(raw, "comments", "comment_count")
        m["forwards"] = _pick(raw, "reposts", "shares", "repost_count")
        m["link_clicks"] = _pick(raw, "link_clicks", "url_clicks")
        return self._apply_field_map(m, raw, field_map)


class YouTubeProvider(BaseProvider):
    """YouTube (видео/Shorts): просмотры, лайки, комментарии, среднее время
    просмотра, длина, шеры, подписки с ролика. Главное поле показов — views."""
    platform = "youtube"

    def normalize(self, raw: dict, field_map: dict = None) -> dict:
        m = model.empty_metrics()
        m["views"] = _pick(raw, "views", "view_count", "video_views")
        m["impressions"] = _pick(raw, "impressions", "impression_count")
        m["avg_watch_sec"] = _pick(raw, "avg_watch_time_sec", "average_view_duration",
                                   "avg_watch_time")
        m["video_len_sec"] = _pick(raw, "video_length_sec", "duration_sec", "duration")
        m["likes"] = _pick(raw, "likes", "like_count")
        m["comments"] = _pick(raw, "comments", "comment_count")
        m["shares"] = _pick(raw, "shares", "share_count")
        m["follows"] = _pick(raw, "subscribers_gained", "follows", "subscribersGained")
        return self._apply_field_map(m, raw, field_map)


class ThreadsProvider(BaseProvider):
    """Threads: просмотры, лайки, ответы (comments), репосты, цитаты, клики."""
    platform = "threads"

    def normalize(self, raw: dict, field_map: dict = None) -> dict:
        m = model.empty_metrics()
        m["views"] = _pick(raw, "views", "view_count")
        m["likes"] = _pick(raw, "likes", "like_count")
        m["comments"] = _pick(raw, "replies", "comments", "comment_count")
        m["forwards"] = _pick(raw, "reposts", "shares", "quotes", "repost_count")
        m["link_clicks"] = _pick(raw, "link_clicks", "url_clicks")
        return self._apply_field_map(m, raw, field_map)


PROVIDERS = {
    InstagramProvider.platform: InstagramProvider(),
    TelegramProvider.platform: TelegramProvider(),
    VkProvider.platform: VkProvider(),
    YouTubeProvider.platform: YouTubeProvider(),
    ThreadsProvider.platform: ThreadsProvider(),
}


def get_provider(platform: str):
    return PROVIDERS.get(str(platform or "").strip().lower())


def fetch_metrics(post: dict, live: bool = False, field_map: dict = None,
                  session=None) -> dict:
    """ЕДИНАЯ точка входа источника. Возвращает словарь-результат:
        {"ok": True,  "metrics": {...}, "source": …, "note": "", "captured_at": …}
        {"ok": False, "metrics": None,  "reason": "..."}

    `source` — откуда реально взяты цифры, без приукрашивания:
        upload_post        живой ответ сервиса;
        upload_post_cache  последний успешный ответ из локального кэша (не свежий);
        sample             образец/ручная выгрузка из карточки поста.

    live=False (умолчание) — нормализуем сырьё из самой карточки, никакой сети.
    live=True — идём в Upload-Post ТОЛЬКО при наличии ключа и ключа связи поста.
    Любой сбой (нет ключа, нет сети, 401/404/429/5xx, пост ещё не в снимках) НЕ
    роняет конвейер: деградируем на кэш → образец → честное «данных нет» с
    причиной по-русски. Платный аккаунт без ключа не трогается.

    `session` — общая `live.LiveSession` на прогон (одна пачка снимков на все
    посты). `field_map` подсказывает главное поле показов площадки."""
    provider = get_provider(post.get("platform"))
    if provider is None:
        return {"ok": False, "metrics": None,
                "reason": "нет провайдера для площадки %r" % post.get("platform")}

    if live:
        try:
            got = (session or _live_module.LiveSession()).fetch(post)
            return {"ok": True,
                    "metrics": provider.normalize(got["raw"], field_map=field_map),
                    "source": got["source"],
                    "captured_at": got.get("captured_at", ""),
                    "note": ("данные из локального кэша последнего успешного "
                             "ответа — не свежие"
                             if got["source"] == "upload_post_cache" else "")}
        except (LiveSourceUnavailable, upload_post.UploadPostError) as ex:
            raw = post.get("raw")
            if isinstance(raw, dict) and raw:
                return {"ok": True, "metrics": provider.normalize(raw),
                        "source": "sample", "captured_at": "",
                        "note": "живой Upload-Post недоступен (%s) — взят образец"
                                % ex}
            return {"ok": False, "metrics": None,
                    "reason": "живых метрик нет (%s), и образца в карточке тоже "
                              "нет — честно «данных нет»" % ex}

    raw = post.get("raw")
    if not isinstance(raw, dict) or not raw:
        return {"ok": False, "metrics": None,
                "reason": "в карточке поста %r нет блока raw с сырыми метриками"
                          % post.get("post_id")}
    return {"ok": True, "metrics": provider.normalize(raw),
            "source": "sample", "captured_at": "", "note": ""}


# --- карта «главное поле показов по площадке» --------------------------------

FIELD_MAP_FIXTURE = "fixtures/platform_metrics.sample.json"


def load_field_map(name: str = FIELD_MAP_FIXTURE) -> dict:
    """{площадка: имя главного поля показов}. Читается из ЛОКАЛЬНОЙ фикстуры рядом
    с кодом: живого эндпоинта `platform-metrics` в текущей спеке Upload-Post НЕТ
    (сверено по openapi.json), поэтому карта у нас своя и правится руками, а не
    тянется по сети. Нет файла/битый → {} (работаем без карты, не падаем)."""
    path = paths.packaged_sample_path(name)
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return {}
    return upload_post.impressions_field_map(data)


# --- загрузка образца-mock ---------------------------------------------------

def load_sample(name: str = None) -> list:
    """Читает упакованный образец метрик (metrics.sample.json рядом с кодом или
    файл, заданный в конфиге analytics_sample_file, — сперва в папке данных
    установки, затем рядом с кодом). Возвращает список карточек постов с блоком
    raw. Нет файла/битый → пустой список (урезанный режим, не падение)."""
    name = name or config.sample_file()
    candidates = [paths.data_dir() / name, paths.packaged_sample_path(name)]
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            return []
        if isinstance(data, dict):
            data = data.get("posts", [])
        return data if isinstance(data, list) else []
    return []
