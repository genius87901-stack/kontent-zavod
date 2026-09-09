# -*- coding: utf-8 -*-
"""Оркестратор ЖИВОГО сбора: «наш пост → метрики Upload-Post», устойчиво.

Почему нужен отдельный слой. В текущей спеке Upload-Post НЕТ эндпоинта «дай
метрики по request_id». Связь строится в два шага:

    request_id (сохранил autopost при публикации)
        → GET /uploadposts/status?request_id=…   → нативный id/ссылка поста
        → GET /uploadposts/post-analytics/cached → снимок метрик этого поста

Пачку `cached` тянем ОДИН РАЗ на прогон и держим в памяти сессии: она отдаёт до
200 постов за запрос, не дёргает площадки и не попадает под лимит 100 запросов /
5 минут. Так `ingest` по десяти постам — это один-два сетевых вызова, а не десять.

УСТОЙЧИВОСТЬ (главное требование): ни один сбой сети/ключа/сервиса не имеет права
уронить конвейер. Порядок деградации, всегда честный и с причиной:

    1. живой ответ Upload-Post                → source="upload_post"
    2. последний успешный ответ из кэша       → source="upload_post_cache"
    3. образец из карточки поста              → source="sample"      (в providers)
    4. честное «данных нет» с причиной        → ok=False             (в providers)

Офлайн-установка клиента (Windows, часто без сети) попадает сразу в п.2/3 и
работает — модуль не падает, а объясняет по-русски, почему цифры не свежие.
"""
from __future__ import annotations

from . import cache, upload_post


class LiveSourceUnavailable(RuntimeError):
    """Живой источник недоступен для этого поста: нет ключа, нет ключа связи
    (request_id/нативный id/ссылка), площадка не покрыта, сеть легла, сервис
    ответил ошибкой. Наверху ловится и деградирует — платный аккаунт не жжём."""


def _cache_name(user, platform):
    return "cached:%s:%s" % (user or "-", platform or "all")


class LiveSession:
    """Одна сессия живого сбора (один прогон `ingest --live`).

    Держит: ключ, профиль, уже вытянутую пачку снимков и индекс по ним, а также
    отметку «данные из кэша, не живьём». Сетевые вызовы делает лениво — если ни
    один пост не годится для живого сбора, в сеть не идём вообще.

    `transport` пробрасывается в `upload_post.set_transport` вызывающим кодом
    (тесты), сама сессия про него ничего не знает."""

    def __init__(self, key=None, user=None, timeout=None, retries=None,
                 max_pages=3, use_cache=True):
        self.key = key or upload_post.read_key()
        self.user = user or upload_post.read_user()
        self.timeout = timeout or upload_post.DEFAULT_TIMEOUT
        self.retries = retries or upload_post.DEFAULT_RETRIES
        self.max_pages = max(1, int(max_pages))
        self.use_cache = use_cache
        self.notes = []            # человеческие пояснения по прогону
        self._index = {}           # опознавательный знак → снимок
        self._loaded = set()       # какие площадки уже тянули
        self._load_error = {}      # площадка → почему пачку не удалось получить
        self._stale = False        # данные взяты из локального кэша
        self._status_cache = {}    # request_id → ответ status (в пределах прогона)

    # --- доступность -------------------------------------------------------

    def available(self):
        """(можно ли идти в живой источник, причина если нельзя). Ключа нет —
        НЕ идём: чужой платный аккаунт вслепую не трогаем."""
        if not self.key:
            return False, ("нет ключа UPLOAD_POST_API_KEY — живой сбор Upload-Post "
                           "недоступен (ключ кладётся как для публикации: "
                           "окружение или .env)")
        if not self.user:
            return False, ("не задан профиль Upload-Post (UPLOAD_POST_USER или "
                           "настройка analytics_profile_username) — без него "
                           "сервис не знает, чью аналитику отдавать")
        return True, ""

    @property
    def stale(self):
        """True, если хоть что-то в этом прогоне взято из локального кэша."""
        return self._stale

    def _note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    # --- пачка снимков -----------------------------------------------------

    def _fetch_pages(self, platform):
        """Тянет cached-страницы (до max_pages) и возвращает список снимков.
        Любая ошибка пробрасывается — обработает `_load`."""
        snapshots, cursor = [], None
        for _ in range(self.max_pages):
            raw = upload_post.cached_post_analytics(
                user=self.user, platform=platform, limit=200, cursor=cursor,
                key=self.key, timeout=self.timeout, retries=self.retries)
            snapshots.extend(upload_post.cached_posts(raw))
            cursor = upload_post.next_cursor(raw)
            if not cursor:
                break
        return snapshots

    def _load(self, platform):
        """Загрузить снимки по площадке в индекс. Живьём, а при сбое — из кэша
        последнего успешного ответа. Не поднимает исключений наружу: если совсем
        ничего нет, индекс просто останется пустым (обработается как «нет данных»)."""
        if platform in self._loaded:
            return
        self._loaded.add(platform)
        name = _cache_name(self.user, platform)
        try:
            snapshots = self._fetch_pages(platform)
            cache.put(name, snapshots)
        except upload_post.UploadPostError as ex:
            snapshots, saved_at = (cache.get(name) if self.use_cache
                                   else (None, None))
            if isinstance(snapshots, list) and snapshots:
                self._stale = True
                self._note("живой Upload-Post не ответил (%s) — взят последний "
                           "успешный ответ из кэша от %s" % (ex, saved_at))
            else:
                # Ни живого ответа, ни кэша. Запоминаем ПРИЧИНУ: без неё пост
                # потом отвалится с бессмысленным «не найден в аналитике», хотя
                # настоящая причина — «сети нет» или «ключ отвергнут».
                self._load_error[platform] = str(ex)
                self._note("живой Upload-Post не ответил (%s), и кэша ещё нет" % ex)
                return
        for snap in snapshots:
            for ref in upload_post.snapshot_keys(snap):
                self._index.setdefault(ref, snap)

    # --- связь «наш пост → снимок» -----------------------------------------

    def _refs_from_status(self, request_id, platform):
        """Нативные id/ссылки поста по нашему request_id. Сбой/`processing` →
        пустое множество (не ошибка: пост мог ещё не выйти)."""
        if not request_id:
            return set()
        if request_id in self._status_cache:
            raw = self._status_cache[request_id]
        else:
            name = "status:%s" % request_id
            try:
                raw = upload_post.upload_status(
                    request_id, key=self.key, timeout=self.timeout,
                    retries=self.retries)
                cache.put(name, raw)
            except upload_post.UploadPostError as ex:
                raw, saved_at = cache.get(name) if self.use_cache else (None, None)
                if raw is None:
                    self._note("статус публикации %s не получен (%s)"
                               % (request_id, ex))
                    return set()
                self._stale = True
                self._note("статус %s взят из кэша от %s" % (request_id, saved_at))
            self._status_cache[request_id] = raw
        state = upload_post.status_state(raw)
        if state == "processing":
            self._note("публикация %s ещё обрабатывается — метрик пока нет"
                       % request_id)
            return set()
        if state == "failed":
            self._note("публикация %s у сервиса помечена как неудачная — метрик "
                       "не будет" % request_id)
            return set()
        return upload_post.post_refs(raw, platform)

    def fetch(self, post: dict) -> dict:
        """Живые метрики ОДНОГО поста.

        Возвращает {"raw": плоский блок метрик, "source": …, "captured_at": …}
        либо поднимает LiveSourceUnavailable с человеческой причиной.
        Порядок поиска ключа связи: явный нативный id / ссылка из карточки →
        разрешение request_id через status. Ни одного ключа нет → в сеть за этим
        постом не идём вообще."""
        ok, why = self.available()
        if not ok:
            raise LiveSourceUnavailable(why)

        platform = str(post.get("platform") or "").strip().lower()
        up_platform = upload_post.PLATFORM_MAP.get(platform)
        if up_platform is None:
            raise LiveSourceUnavailable(
                "Upload-Post не отдаёт аналитику для площадки %r — живого "
                "источника нет" % platform)

        refs = set()
        for keyname in ("platform_post_id", "native_post_id", "media_id"):
            val = post.get(keyname)
            if val:
                refs.add(str(val).strip())
        if post.get("url"):
            refs.add(upload_post.normalize_url(post["url"]))
        request_id = post.get("request_id") or post.get("upload_request_id")

        if not refs and not request_id:
            raise LiveSourceUnavailable(
                "у поста %r нет ни request_id, ни ссылки/нативного id — связать "
                "его с Upload-Post нечем (request_id сохраняет публикация в "
                ".state/published_ids.jsonl)" % post.get("post_id"))

        self._load(up_platform)
        if up_platform in self._load_error:
            # Пачку получить не удалось вообще — называем НАСТОЯЩУЮ причину
            # (нет сети / ключ отвергнут / сервис лёг), а не «пост не найден».
            raise LiveSourceUnavailable(self._load_error[up_platform])
        snap = None
        for ref in refs:
            if ref in self._index:
                snap = self._index[ref]
                break
        if snap is None and request_id:
            for ref in self._refs_from_status(request_id, up_platform):
                if ref in self._index:
                    snap = self._index[ref]
                    break

        if snap is None:
            raise LiveSourceUnavailable(
                "пост %r не найден в аналитике Upload-Post (пост мог выйти совсем "
                "недавно и ещё не попасть в снимки, либо площадка не отдаёт по "
                "нему статистику)" % post.get("post_id"))

        metrics = upload_post.snapshot_metrics(snap)
        if not metrics:
            raise LiveSourceUnavailable(
                "Upload-Post нашёл пост %r, но метрик по нему не отдал (пусто)"
                % post.get("post_id"))
        return {"raw": metrics,
                "source": "upload_post_cache" if self._stale else "upload_post",
                "captured_at": snap.get("captured_at") or "",
                "post_url": snap.get("post_url") or ""}
