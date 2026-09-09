# -*- coding: utf-8 -*-
"""Офлайн-тесты живого сбора Upload-Post: ЗАПИСАННЫЕ ответы → парсинг → метрики.

НИ ОДНОГО СЕТЕВОГО БАЙТА. Транспорт подменяется через `upload_post.set_transport`,
ответы берутся из `fixtures/` (реалистичные по openapi.json docs.upload-post.com).
Платные вызовы Upload-Post отсюда не делаются и делаться не должны.

Папка данных на время теста уводится во временный каталог, чтобы прогон не трогал
`analytics/` реальной установки (кэш, история, хранилище).

Запуск:
    python -m analytics.test_live
    python modules/analytics/test_live.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

try:
    from . import (cache, history, live, model, paths, providers, published,
                   upload_post)
except ImportError:                                   # запуск файлом напрямую
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from analytics import (cache, history, live, model, paths, providers,
                           published, upload_post)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  %s %s%s" % ("OK  " if cond else "ПАДЕНИЕ", name,
                         "" if cond else "  → " + str(detail)))


def fixture(name):
    with (FIXTURES / name).open(encoding="utf-8-sig") as fh:
        return json.load(fh)


# --- подмена окружения --------------------------------------------------------

class Sandbox:
    """Уводит данные модуля во временную папку и подменяет ключ/профиль/транспорт.
    Реальную установку и реальный аккаунт прогон не трогает."""

    def __init__(self, transport=None, key="TEST-KEY-NOT-REAL", user="demo_profile"):
        self.transport = transport
        self.key, self.user = key, user
        self._tmp = None

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old_data_dir = paths.data_dir
        self._old_pub = paths.published_log_path
        paths.data_dir = lambda: tmp / "analytics"
        paths.published_log_path = lambda: tmp / ".state" / "published_ids.jsonl"
        self._old_key = upload_post.read_key
        self._old_user = upload_post.read_user
        upload_post.read_key = lambda: self.key
        upload_post.read_user = lambda: self.user
        self._old_transport = upload_post.set_transport(self.transport)
        self._old_sleep = upload_post._sleep
        upload_post._sleep = lambda s: None            # тесты не ждут по-настоящему
        return tmp

    def __exit__(self, *exc):
        paths.data_dir = self._old_data_dir
        paths.published_log_path = self._old_pub
        upload_post.read_key = self._old_key
        upload_post.read_user = self._old_user
        upload_post.set_transport(self._old_transport)
        upload_post._sleep = self._old_sleep
        self._tmp.cleanup()
        return False


def recorded_transport(routes, log=None):
    """Транспорт на записанных ответах. routes: {кусок пути: ответ|исключение|
    callable}. Каждый вызов пишется в `log` — так тест доказывает, СКОЛЬКО именно
    сетевых обращений сделал бы модуль."""
    def _t(path, params, key, timeout):
        if log is not None:
            log.append((path, dict(params or {})))
        for frag, resp in routes.items():
            if frag in path:
                if callable(resp):
                    return resp(path, params, key, timeout)
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise upload_post.UploadPostNotFound("нет записанного ответа для %s" % path)
    return _t


IG_POST = {"post_id": "c38_instagram", "platform": "instagram",
           "format": "carousel", "request_id": "req_demo_0001",
           "user": "demo_profile", "topic": "пост-достигаторство"}


def cached_paged(path, params, key, timeout):
    """Записанный cached-ответ с ЧЕСТНОЙ постраничностью: первая страница отдаёт
    курсор, по курсору приходит пустой хвост. Иначе клиент крутил бы один и тот
    же курсор до упора в max_pages — именно это тест и должен ловить."""
    if (params or {}).get("cursor"):
        return {"source": "snapshot_cache", "posts": [], "next_cursor": None,
                "has_more": False}
    return fixture("cached_analytics.sample.json")


def routes_ok():
    return {"/uploadposts/status": fixture("status.sample.json"),
            "/uploadposts/post-analytics/cached": cached_paged}


# --- 1. разбор записанного ответа --------------------------------------------

def test_parse_cached():
    raw = fixture("cached_analytics.sample.json")
    posts = upload_post.cached_posts(raw)
    check("cached: из ответа достаются снимки постов", len(posts) == 3, posts)
    check("cached: курсор следующей страницы виден",
          upload_post.next_cursor(raw) == "eyJvZmZzZXQiOjN9")
    ig = posts[0]
    m = upload_post.snapshot_metrics(ig)
    check("cached: блок метрик отделён от служебных полей",
          "views" in m and "post_id" not in m, m)
    keys = upload_post.snapshot_keys(ig)
    check("cached: опознавательные знаки — нативный id и нормализованная ссылка",
          "17912345678901234" in keys and "instagram.com/p/dabc123xyz" in keys, keys)


def test_parse_status():
    raw = fixture("status.sample.json")
    check("status: состояние публикации прочитано",
          upload_post.status_state(raw) == "completed")
    refs = upload_post.post_refs(raw, "instagram")
    check("status: из ответа извлечён нативный id поста",
          "17912345678901234" in refs, refs)
    check("status: из ответа извлечена ссылка (нормализованная)",
          "instagram.com/p/dabc123xyz" in refs, refs)
    proc = {"request_id": "r", "status": "processing", "result": {}}
    check("status: «ещё обрабатывается» распознано",
          upload_post.status_state(proc) == "processing")


def test_url_normalize():
    a = upload_post.normalize_url("https://www.instagram.com/p/DABC123xyz/?igshid=42")
    b = upload_post.normalize_url("http://instagram.com/p/dabc123xyz")
    check("ссылки IG с ?igshid и без сравниваются одинаково", a == b, (a, b))


# --- 2. записанный ответ → нормализованные метрики ---------------------------

def test_live_to_metrics():
    calls = []
    with Sandbox(recorded_transport(routes_ok(), calls)):
        session = live.LiveSession()
        res = providers.fetch_metrics(IG_POST, live=True,
                                      field_map=providers.load_field_map(),
                                      session=session)
        check("живой сбор: результат ok и источник — живой Upload-Post",
              res["ok"] and res["source"] == "upload_post", res)
        m = res["metrics"]
        check("метрики: просмотры разобраны", m["views"] == 45230, m)
        check("метрики: охват разобран", m["reach"] == 41880, m)
        check("метрики: лайки разобраны", m["likes"] == 1204, m)
        check("метрики: комментарии разобраны", m["comments"] == 47, m)
        check("метрики: сохранения разобраны (saved → saves)", m["saves"] == 212, m)
        check("метрики: переходы по ссылке разобраны (website_clicks)",
              m["link_clicks"] == 91, m)
        check("метрики: момент снятия проброшен",
              res["captured_at"].startswith("2026-08-01"), res)

        rec = model.new_record(IG_POST, m, res["source"])
        check("схема: запись валидна", model.validate(rec) == [], model.validate(rec))
        d = rec["derived"]
        check("ставки: вовлечённость посчитана от охвата",
              d["engagement_rate_pct"] == round(
                  (1204 + 47 + 212 + 168) / 41880 * 100, 2), d)
        check("ставки: сохранения посчитаны", d["save_rate_pct"] == round(
            212 / 41880 * 100, 2), d)


def test_one_batch_for_many_posts():
    """Десять постов одной площадки — это ОДИН запрос пачки, а не десять."""
    calls = []
    cached = fixture("cached_analytics.sample.json")
    with Sandbox(recorded_transport(routes_ok(), calls)):
        session = live.LiveSession()
        posts = [dict(IG_POST, post_id="p%d" % i,
                      platform_post_id=cached["posts"][i % 3]["post_id"])
                 for i in range(10)]
        oks = [providers.fetch_metrics(p, live=True, session=session)["ok"]
               for p in posts]
        cached_calls = [c for c in calls if "cached" in c[0]]
        # Две страницы = ОДНА полная выгрузка пачки. Важно, что число запросов
        # не зависит от числа постов: 10 постов — те же 2 запроса, а не 10.
        check("пачка снимков тянется один раз на прогон (2 страницы, не 10 запросов)",
              len(cached_calls) == 2, calls)
        check("все 10 постов получили метрики из одной пачки", all(oks), oks)


def test_telegram_and_vk():
    with Sandbox(recorded_transport(routes_ok())):
        vk = {"post_id": "c38_vk", "platform": "vk", "request_id": "req_demo_0001",
              "raw": {"views": 900, "likes": 30}}
        res = providers.fetch_metrics(vk, live=True)
        check("VK: сервис площадку не покрывает → честный откат на образец",
              res["ok"] and res["source"] == "sample" and "не отдаёт" in res["note"],
              res)


# --- 3. устойчивость: нет ключа / нет сети / ошибки API ----------------------

def test_no_key():
    calls = []
    with Sandbox(recorded_transport(routes_ok(), calls), key=None):
        post = dict(IG_POST, raw={"reach": 100, "impressions": 120})
        res = providers.fetch_metrics(post, live=True)
        check("нет ключа: не падаем, откат на образец", res["ok"], res)
        check("нет ключа: источник честно помечен как образец",
              res["source"] == "sample" and "UPLOAD_POST_API_KEY" in res["note"], res)
        check("нет ключа: в сеть не пошли ВООБЩЕ", calls == [], calls)


def test_offline():
    """Офлайн-установка клиента: сети нет совсем."""
    boom = upload_post.UploadPostOffline("нет связи с Upload-Post: [Errno 8] nodename")
    calls = []
    with Sandbox(recorded_transport({"/": boom}, calls)):
        post = dict(IG_POST, raw={"reach": 100, "impressions": 120, "likes": 5})
        res = providers.fetch_metrics(post, live=True)
        check("нет сети: не падаем, откат на образец",
              res["ok"] and res["source"] == "sample", res)
        check("нет сети: причина названа человеческим языком",
              "не ответил" in res["note"] or "нет связи" in res["note"], res)

        bare = dict(IG_POST)                       # образца в карточке тоже нет
        res2 = providers.fetch_metrics(bare, live=True)
        check("нет сети и нет образца: честное «данных нет», а не исключение",
              res2["ok"] is False and "данных нет" in res2["reason"], res2)


def test_cache_fallback():
    """Сначала успешный прогон, потом сеть легла — берём последний успешный ответ."""
    with Sandbox(recorded_transport(routes_ok())) as tmp:
        live.LiveSession().fetch(IG_POST)                     # прогрели кэш
        stamp = cache.last_success()
        check("кэш: отметка последнего успешного обновления записана",
              bool(stamp), stamp)

        boom = upload_post.UploadPostOffline("сеть отвалилась")
        old = upload_post.set_transport(recorded_transport(
            {"/uploadposts/status": fixture("status.sample.json"),
             "/uploadposts/post-analytics/cached": boom}))
        try:
            res = providers.fetch_metrics(IG_POST, live=True)
            check("кэш: при обрыве сети метрики взяты из кэша, не из образца",
                  res["ok"] and res["source"] == "upload_post_cache", res)
            check("кэш: пользователю честно сказано, что данные не свежие",
                  "не свежие" in res["note"], res)
            check("кэш: цифры те же, что были живьём", res["metrics"]["views"] == 45230,
                  res)
        finally:
            upload_post.set_transport(old)


def test_api_errors():
    cases = [
        (upload_post.UploadPostAuthRejected("HTTP 401"), "ключ отвергнут (401)"),
        (upload_post.UploadPostRateLimited("HTTP 429"), "лимит запросов (429)"),
        (upload_post.UploadPostServerError("HTTP 503"), "сбой сервиса (5xx)"),
        (upload_post.UploadPostBadResponse("не-JSON"), "битый ответ"),
    ]
    for exc, label in cases:
        with Sandbox(recorded_transport({"/": exc})):
            post = dict(IG_POST, raw={"reach": 10, "likes": 1})
            res = providers.fetch_metrics(post, live=True)
            check("API ответил ошибкой — %s: конвейер не падает" % label,
                  res["ok"] and res["source"] == "sample", res)


def test_empty_answer():
    empty = {"source": "snapshot_cache", "posts": [], "next_cursor": None,
             "has_more": False}
    with Sandbox(recorded_transport({"/uploadposts/status":
                                     fixture("status.sample.json"),
                                     "/uploadposts/post-analytics/cached": empty})):
        post = dict(IG_POST, raw={"reach": 10, "likes": 1})
        res = providers.fetch_metrics(post, live=True)
        check("API вернул пусто: откат на образец с внятной причиной",
              res["ok"] and res["source"] == "sample" and "не найден" in res["note"],
              res)


def test_retry_backoff():
    """5xx повторяем с бэкоффом, 401 — не повторяем (не жжём лимит зря)."""
    state = {"n": 0}

    def flaky(path, params, key, timeout):
        state["n"] += 1
        if state["n"] < 3:
            raise upload_post.UploadPostServerError("HTTP 503")
        return fixture("cached_analytics.sample.json")

    old = upload_post.set_transport(None)            # ретраи живут в _request
    try:
        upload_post.set_transport(None)
        real = upload_post._http_get
        upload_post._http_get = flaky
        old_sleep, upload_post._sleep = upload_post._sleep, lambda s: None
        try:
            out = upload_post._request("/uploadposts/post-analytics/cached",
                                       key="X", retries=3)
            check("ретраи: два 503 подряд пережиты, с третьей попытки успех",
                  state["n"] == 3 and upload_post.cached_posts(out), state)

            state["n"] = 0

            def rejected(*a, **kw):
                state["n"] += 1
                raise upload_post.UploadPostAuthRejected("HTTP 401")

            upload_post._http_get = rejected
            try:
                upload_post._request("/x", key="X", retries=3)
                check("ретраи: 401 не повторяется", False, "исключения не было")
            except upload_post.UploadPostAuthRejected:
                check("ретраи: 401 не повторяется (одна попытка, лимит не жжём)",
                      state["n"] == 1, state)
        finally:
            upload_post._http_get = real
            upload_post._sleep = old_sleep
    finally:
        upload_post.set_transport(old)


# --- 4. история (динамика, а не только срез) ---------------------------------

def test_history():
    with Sandbox(recorded_transport(routes_ok())):
        rec1 = model.new_record(IG_POST, dict(model.empty_metrics(), reach=1000,
                                              likes=10), "upload_post")
        check("история: первый замер записан", history.append(rec1) is True)
        check("история: тот же замер повторно не пишется",
              history.append(rec1) is False)
        d = history.delta("c38_instagram")
        check("история: по одному замеру динамики честно нет",
              d["ok"] is False and "один" in d["reason"], d)

        rec2 = model.new_record(IG_POST, dict(model.empty_metrics(), reach=1500,
                                              likes=18), "upload_post")
        history.append(rec2)
        d = history.delta("c38_instagram")
        check("история: прирост охвата посчитан",
              d["ok"] and d["growth"] == 500 and d["growth_pct"] == 50.0, d)
        s = history.summary()
        check("история: сводка считает замеры и посты",
              s["measurements"] == 2 and s["posts"] == 1, s)


# --- 5. шов с публикацией ----------------------------------------------------

def test_published_seam():
    with Sandbox(recorded_transport(routes_ok())) as tmp:
        log = tmp / ".state" / "published_ids.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"request_id": "req_demo_0001", "platform": "instagram",
             "platforms": ["instagram", "telegram"], "user": "demo_profile",
             "post_id": "c38_instagram", "material": "c38", "media_type": "photos",
             "published_at": "2026-07-30T09:00:00+00:00", "status": "completed"},
            {"request_id": "req_demo_0001", "platform": "telegram",
             "platforms": ["instagram", "telegram"], "user": "demo_profile",
             "post_id": "c38_telegram", "material": "c38", "media_type": "photos",
             "published_at": "2026-07-30T09:00:00+00:00", "status": "completed"},
            {"битая": "строка без request_id"},
        ]
        log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                       + "\nне json вовсе\n", encoding="utf-8")
        cards = published.to_cards()
        check("шов: журнал публикаций прочитан, битые строки пропущены",
              len(cards) == 2, cards)
        check("шов: request_id и площадка попали в карточку",
              cards[0]["request_id"] == "req_demo_0001"
              and cards[0]["platform"] == "instagram", cards[0])
        check("шов: формат выведен из media_type (photos → carousel)",
              cards[0]["format"] == "carousel", cards[0])

        res = providers.fetch_metrics(cards[0], live=True, session=live.LiveSession())
        check("шов: по одному только request_id живые метрики найдены",
              res["ok"] and res["source"] == "upload_post"
              and res["metrics"]["views"] == 45230, res)

        # Журнала нет вовсе — не падаем.
        log.unlink()
        check("шов: журнала нет → пустой список, не исключение",
              published.to_cards() == [])


def test_no_link_key():
    calls = []
    with Sandbox(recorded_transport(routes_ok(), calls)):
        orphan = {"post_id": "ручной_пост", "platform": "instagram",
                  "format": "carousel", "raw": {"reach": 50, "likes": 3}}
        res = providers.fetch_metrics(orphan, live=True)
        check("нет request_id/ссылки: в сеть за постом не идём",
              calls == [], calls)
        check("нет request_id/ссылки: честный откат на образец",
              res["ok"] and res["source"] == "sample", res)


def main():
    print("=== Офлайн-тесты живого сбора Upload-Post (записанные ответы) ===")
    print("Сеть НЕ используется: транспорт подменён, ответы из fixtures/.\n")
    for group, tests in [
        ("1. Разбор записанных ответов API",
         [test_parse_cached, test_parse_status, test_url_normalize]),
        ("2. Ответ API → нормализованные метрики",
         [test_live_to_metrics, test_one_batch_for_many_posts,
          test_telegram_and_vk]),
        ("3. Устойчивость: нет ключа / нет сети / ошибки API",
         [test_no_key, test_offline, test_cache_fallback, test_api_errors,
          test_empty_answer, test_retry_backoff]),
        ("4. История замеров (динамика)", [test_history]),
        ("5. Шов с публикацией по request_id",
         [test_published_seam, test_no_link_key]),
    ]:
        print(group)
        for t in tests:
            try:
                t()
            except Exception as ex:
                check("%s — упал с исключением" % t.__name__, False, repr(ex))
        print()

    print("Итого: успешно %d, провалено %d." % (len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print("  ! " + name)
        return 1
    print("Все проверки прошли. Ни одного сетевого вызова не сделано.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
