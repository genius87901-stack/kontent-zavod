# -*- coding: utf-8 -*-
"""СТЕНД СРАВНЕНИЯ ГЕНЕРАТОРОВ ВИДЕО И КАРТИНОК.

ЗАЧЕМ ОН. Перебивки в роликах сейчас берутся со стока (Pixabay). Сток дешёвый
(бесплатный), но чужой: кадр «женщина у ноутбука» один и тот же у нас и у
десяти тысяч других. Генерация решает это, но стоит денег. Вопрос, на который
отвечает стенд, ровно один: **за какое качество вообще стоит платить и сколько
это стоит на НАШЕМ материале.** Не «какая модель лучше вообще», а «какая модель
лучше на тех запросах, которые реально приходят из наших роликов».

ГЛАВНОЕ ОГРАНИЧЕНИЕ — ДЕНЬГИ. Платит не студия, а каждый клиент сам: своим
ключом и своей картой. Поэтому:
  * подписки не рассматриваем, только разовое пополнение баланса
    (pay-as-you-go). fal.ai так и работает: кладёшь сумму на счёт, она тратится
    по факту; деньги списываются только за УСПЕШНЫЕ результаты — за ошибки
    сервера и ожидание в очереди не берут;
  * стенд по умолчанию НИЧЕГО не тратит (`--dry-run`), только считает смету;
  * есть жёсткий потолок в долларах (`--max-usd`), выше которого прогон просто
    не стартует, а начатый — останавливается. Потолок СКВОЗНОЙ: он считает всё,
    что стенд потратил за всё время (по журналу), а не каждый запуск заново —
    иначе пять запусков подряд это пять раз по потолку, о чём никто не
    предупреждал;
  * повторный прогон того же запроса на той же модели платит НОЛЬ: результат
    лежит в кэше (тот же приём, что `.cache_stock` в stock.py).

ЧЕМ СРАВНИВАЕМ. Два файла в `out_bench/`, оба про одно и то же:
  * `СРАВНЕНИЕ.html` — для ЧЕЛОВЕКА. Открывается двойным щелчком, клипы играют
    прямо в странице, всё внутри одного файла (ни одной внешней ссылки — можно
    переслать, унести на флешке, смотреть без интернета). Строка — наш запрос,
    первым идёт нынешний сток Pixabay, дальше модели, под каждым клипом — цена
    именно этой штуки в долларах и рублях;
  * `СРАВНЕНИЕ.jpg` — тот же расклад одной картинкой, когда нужно вложить в
    переписку или распечатать.
Без столбца «а что отдаёт сток сейчас» сравнение бессмысленно: непонятно,
лучше стало или просто дороже. Поэтому Pixabay подмешивается всегда.

КАК ДОБАВИТЬ НОВЫЙ СЕРВИС. Приём взят у stock.py (`search_pixabay`/
`search_pexels` + словарь `PROVIDERS`): одна функция-генератор + одна запись в
словаре, остальной код не трогается.
    1) написать `gen_<сервис>(model, query, dest, timeout)` → путь к файлу;
    2) вписать её в `PROVIDERS`;
    3) вписать ключи в `KEYS` (имя переменной + человеческая подсказка, где взять);
    4) добавить модели в `MODELS` с ценой и единицей тарификации.

ЗАПУСК:
    python bench_generate.py --list-models        # прайс и видно ли ключ
    python bench_generate.py -n 5 --models ltx-video,kling-standard
                                                  # смета, ни копейки не тратится
    python bench_generate.py -n 5 --models ltx-video,kling-standard --spend --max-usd 2
                                                  # платный прогон, потолок $2
Последняя строка — весь тест целиком одной командой: она сходит в сервис,
сложит клипы, посчитает деньги и соберёт страницу сравнения. Больше ничего
делать не нужно.

КЛЮЧИ — в `.env` рядом с этим файлом, по строке на ключ (формат stock.py).
Руками в переменные окружения ничего прописывать не надо, файла достаточно:
    FAL_KEY=...
    PIXABAY_API_KEY=...
Ключей может не быть вовсе — стенд от этого не ломается: смета считается всегда,
а при попытке потратить он говорит по-русски, какого ключа не хватает и где его
взять.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import html
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ── ПУТИ ─────────────────────────────────────────────────────────────────────
# Никаких абсолютных путей (CONTRACT.md §3): всё от каталога этого файла.
HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
OUT_DIR = HERE / "out_bench"           # результаты прогона + контрольный лист
CACHE_DIR = HERE / ".cache_bench"      # «уже сгенерировано, второй раз не платим»
JOURNAL = "journal.jsonl"              # внутри OUT_DIR
SHEET = "СРАВНЕНИЕ.jpg"                # внутри OUT_DIR
PAGE = "СРАВНЕНИЕ.html"                # внутри OUT_DIR — главный файл для человека

#: Потолок на ОДИН вложенный в страницу файл. Страница самодостаточна: клипы
#: лежат внутри неё, а не ссылками, — иначе она перестанет открываться, стоит
#: унести её на другую машину или в переписку. Плата за это — размер, поэтому
#: слишком тяжёлый клип в страницу не вшивается: показываем кадр и путь к файлу.
MAX_EMBED_MB = 25

UA = "karusel-bench/1.0 (content factory benchmark)"

#: Кадр из клипа для контрольного листа берём на этой секунде: на нулевой у
#: генеративного видео часто ещё «раскачивается» первый кадр.
PREVIEW_AT = 1.0

#: Дата, на которую сверялся прайс. Цены у генераторов меняются часто, и
#: молчаливо устаревший прайс — худшее, что может быть в смете: потолок трат
#: считает деньги по ЭТОЙ табличке, а списывает их сервис по своей. Занижена
#: цена здесь — стенд честно думает, что уложился в потолок, а счёт придёт
#: больше. Поэтому дата печатается рядом с каждым итогом, а тест
#: `test_прайс_совпадает_с_датой_сверки` не даёт поменять цену, не двинув дату.
#: Сверка 2026-08-23: цены сняты со страниц самих моделей на fal.ai, каждая
#: цитата — в комментарии у своей записи в MODELS.
PRICES_CHECKED = "2026-08-23"

#: Курс ЦБ РФ на дату сверки (cbr.ru/scripts/XML_daily.asp?date_req=23/08/2026).
#: Нужен ровно затем, чтобы владелец видел смету в тех деньгах, которыми думает.
#: Курс живёт своей жизнью — цифра справочная, счёт всё равно в долларах.
RUB_PER_USD = 82.9211


def rub(usd) -> float:
    """Доллары → рубли по зашитому курсу. Справочно, для человека."""
    return float(usd) * RUB_PER_USD


# ── ОШИБКИ ───────────────────────────────────────────────────────────────────
class NoKey(Exception):
    """Нет ключа. Текст — готовое объяснение человеку, не стектрейс."""


class NotConnected(Exception):
    """Сервис в списке есть, но генератор к нему не написан."""


class GenFailed(Exception):
    """Сервис ответил отказом. Деньги за такое не списываются."""


# ── КЛЮЧИ ────────────────────────────────────────────────────────────────────
def load_env(path=ENV_FILE) -> dict:
    """.env как есть: KEY=VALUE построчно, # — комментарий. Копия приёма stock.py:
    отдельной зависимости ради пяти строк не заводим."""
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


#: Что нужно каждому сервису и где это взять. `нужны` — переменные окружения,
#: `где` — инструкция для человека, который не знает слова «токен».
KEYS = {
    "fal": {
        "нужны": ["FAL_KEY"],
        "где": ("Завести аккаунт на fal.ai, зайти в раздел Keys, нажать «создать "
                "ключ» и скопировать длинную строку. Деньги: там же, в Billing, "
                "пополнить счёт картой на нужную сумму — это разовое пополнение, "
                "не ежемесячная подписка. Списывается только за удачные картинки "
                "и клипы; за ошибки сервиса и ожидание в очереди денег не берут."),
    },
    "pixabay": {
        "нужны": ["PIXABAY_API_KEY"],
        "где": ("Бесплатно. Зайти на pixabay.com под своей учёткой и открыть "
                "https://pixabay.com/api/docs/ — ключ показан прямо на странице. "
                "Одобрения ждать не надо. У нас он уже заведён."),
    },
    "fusionbrain": {
        "нужны": ["FUSIONBRAIN_API_KEY", "FUSIONBRAIN_SECRET_KEY"],
        "где": ("fusionbrain.ai → раздел API → выдаются ДВЕ строки: ключ и "
                "секрет. Российский сервис (Kandinsky от Сбера), оплата рублями "
                "и российскими картами."),
    },
    "yandexart": {
        "нужны": ["YANDEX_API_KEY", "YANDEX_FOLDER_ID"],
        "где": ("Яндекс Облако: завести платёжный аккаунт, создать сервисный "
                "аккаунт с ролью ai.imageGeneration.user, выпустить ему API-ключ. "
                "Второе значение — идентификатор каталога, он виден в адресной "
                "строке консоли. Оплата рублями с российской карты."),
    },
}


def key_for(provider, env=None) -> dict:
    """Ключи сервиса или понятный по-русски отказ.

    ОТЛИЧИЕ ОТ stock.py. Там `key_for` делает `sys.exit` — сток без ключа
    бесполезен. Здесь наоборот: главный режим стенда (`--dry-run`) обязан
    работать вообще без единого ключа, поэтому отсутствие ключа — обычное
    исключение, которое ловится и печатается строкой в таблице, а не смерть
    процесса.
    """
    env = load_env() if env is None else env
    spec = KEYS.get(provider)
    if not spec:
        raise NoKey("Сервис «%s» не описан в KEYS — нечего искать." % provider)
    found, missing = {}, []
    for name in spec["нужны"]:
        val = env.get(name) or os.environ.get(name)
        if val:
            found[name] = val
        else:
            missing.append(name)
    if missing:
        raise NoKey("не хватает %s. Где взять: %s" % (" и ".join(missing), spec["где"]))
    return found


# ── СЕТЬ ─────────────────────────────────────────────────────────────────────
def _request(url, data=None, headers=None, method=None, timeout=120):
    """Один HTTP-запрос с человеческими объяснениями кодов ответа.

    Никакого кэша здесь нет намеренно: кэшируется РЕЗУЛЬТАТ генерации (файл),
    а не HTTP-ответ. Ответ очереди — одноразовый, кэшировать его вредно.
    """
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", "replace")[:400]
        except Exception:                                    # noqa: BLE001
            pass
        raise GenFailed("%s ответил %s %s. %s%s" % (
            urllib.parse.urlsplit(url).netloc, ex.code, ex.reason,
            _http_hint(ex.code), ("\n    ответ сервиса: " + body) if body else ""))
    except urllib.error.URLError as ex:
        raise GenFailed("сеть недоступна: %s" % (ex.reason,))


def _http_hint(code) -> str:
    """Код ответа → что это значит для человека."""
    return {
        401: "Ключ не принят. У fal.ai заголовок пишется словом Key, не Bearer.",
        403: "Ключ есть, но доступа к этой модели нет.",
        402: "На счёте сервиса кончились деньги — надо пополнить баланс.",
        404: "Такой модели у сервиса нет: идентификатор устарел. "
             "Проверь актуальный на fal.ai/models и передай --endpoint.",
        422: "Сервис не принял параметры запроса (например, длительность или "
             "соотношение сторон, которых у этой модели нет).",
        429: "Упёрлись в лимит запросов. Подожди минуту или уменьши --jobs.",
    }.get(code, "Это ответ сервиса, не наша ошибка." if code >= 500 else "")


def _json(url, data=None, headers=None, method=None, timeout=120):
    raw = _request(url, data=data, headers=headers, method=method, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as ex:                                  # noqa: BLE001
        raise GenFailed("сервис вернул не JSON: %s" % ex)


def _download(url, dest: Path, timeout=300) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = _request(url, timeout=timeout)
    dest.write_bytes(raw)
    return dest


#: Настоящая сетевая функция, запомненная при импорте. По ней отказ в main()
#: ПРОВЕРЯЕТ, а не спрашивает: если тест подменил `_request` растяжкой, деньги
#: двинуться физически не могут, и платный путь ему открыт. Галочку-исключение
#: сюда ставить нельзя — её однажды поставит тест, который ничего не подменил.
_НАСТОЯЩИЙ_ЗАПРОС = _request


# ── ГЕНЕРАТОРЫ ───────────────────────────────────────────────────────────────
# Контракт всех генераторов один: gen(model, query, dest_base, timeout) → Path.
# `model` — запись из MODELS, `dest_base` — путь БЕЗ расширения (расширение
# знает только генератор: у видео mp4, у картинок jpg/png).

def gen_fal(model, query, dest_base: Path, timeout=600) -> Path:
    """fal.ai через очередь. Один ключ открывает и картинки, и видео —
    поэтому именно он основной кандидат: клиенту заводить один счёт, а не пять.

    Очередь работает так: кладём задачу → получаем request_id и адреса статуса
    и результата → опрашиваем статус, пока не COMPLETED → забираем результат.
    Адреса статуса/результата берём ИЗ ОТВЕТА (status_url/response_url), а не
    склеиваем сами: так стенд переживёт смену формы путей у fal.
    """
    keys = key_for("fal")
    head = {"Authorization": "Key " + keys["FAL_KEY"],
            "Content-Type": "application/json"}
    body = dict(model["params"])
    body["prompt"] = query

    submitted = _json("https://queue.fal.run/" + model["endpoint"],
                      data=json.dumps(body).encode("utf-8"),
                      headers=head, method="POST", timeout=60)
    status_url = submitted.get("status_url")
    result_url = submitted.get("response_url")
    rid = submitted.get("request_id")
    if not status_url or not result_url:
        if not rid:
            raise GenFailed("fal.ai не вернул ни request_id, ни адресов очереди: %s"
                            % json.dumps(submitted, ensure_ascii=False)[:300])
        base = "https://queue.fal.run/%s/requests/%s" % (model["endpoint"], rid)
        status_url, result_url = base + "/status", base

    deadline = time.time() + timeout
    while True:
        st = _json(status_url, headers=head, timeout=60)
        state = st.get("status", "")
        if state == "COMPLETED":
            break
        if state not in ("IN_QUEUE", "IN_PROGRESS"):
            raise GenFailed("fal.ai вернул неожиданный статус «%s»" % state)
        if time.time() > deadline:
            raise GenFailed("fal.ai не успел за %d с (модель %s). Деньги за "
                            "незавершённое не списываются." % (timeout, model["endpoint"]))
        time.sleep(3)

    res = _json(result_url, headers=head, timeout=120)
    url = _fal_pick_url(res, model["kind"])
    if not url:
        raise GenFailed("fal.ai завершил задачу, но ссылки на файл в ответе нет: %s"
                        % json.dumps(res, ensure_ascii=False)[:300])
    return _download(url, dest_base.with_suffix(_ext_from_url(url, model["kind"])))


def _fal_pick_url(res, kind):
    """Где у fal лежит файл: у видео — video.url, у картинок — images[0].url."""
    if kind == "video":
        vid = res.get("video") or {}
        if isinstance(vid, dict) and vid.get("url"):
            return vid["url"]
        for item in res.get("videos") or []:
            if isinstance(item, dict) and item.get("url"):
                return item["url"]
        return None
    for item in res.get("images") or []:
        if isinstance(item, dict) and item.get("url"):
            return item["url"]
    img = res.get("image")
    if isinstance(img, dict):
        return img.get("url")
    return None


def _ext_from_url(url, kind):
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    if ext in (".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp"):
        return ext
    return ".mp4" if kind == "video" else ".jpg"


def gen_pixabay(model, query, dest_base: Path, timeout=300) -> Path:
    """НЫНЕШНИЙ сток — точка отсчёта, а не участник гонки.

    Он в стенде обязателен: без столбца «а что было раньше» глазами непонятно,
    выросло качество или просто вырос счёт. Цена его — ноль, ключ бесплатный,
    и он у нас уже есть. Эндпоинты те же, что использует завод (stock.py для
    фото, talk_broll.PixabaySource для видео) — специально, чтобы сравнение шло
    с тем, что реально попадает в ролик, а не с абстрактным «стоком».
    """
    keys = key_for("pixabay")
    if model["kind"] == "video":
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode({
            "key": keys["PIXABAY_API_KEY"], "q": query, "video_type": "film",
            "per_page": 3, "safesearch": "true", "order": "popular"})
        hits = _json(url, timeout=60).get("hits", [])
        if not hits:
            raise GenFailed("Pixabay ничего не нашёл по запросу «%s»" % query)
        v = hits[0].get("videos", {})
        pick = v.get("large") or v.get("medium") or v.get("small") or v.get("tiny")
        if not pick or not pick.get("url"):
            raise GenFailed("Pixabay отдал запись без файла")
        return _download(pick["url"], dest_base.with_suffix(".mp4"), timeout)
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": keys["PIXABAY_API_KEY"], "q": query, "image_type": "photo",
        "orientation": "vertical", "per_page": 3, "safesearch": "true",
        "order": "popular"})
    hits = _json(url, timeout=60).get("hits", [])
    if not hits:
        raise GenFailed("Pixabay ничего не нашёл по запросу «%s»" % query)
    src = hits[0].get("largeImageURL") or hits[0].get("webformatURL")
    return _download(src, dest_base.with_suffix(".jpg"), timeout)


def gen_fusionbrain(model, query, dest_base: Path, timeout=600) -> Path:
    """Fusion Brain / Kandinsky — ЗАГЛУШКА, генератор не написан.

    Почему не подключён. Формат у сервиса нетипичный: не JSON, а
    multipart/form-data, где параметры лежат отдельной частью-файлом, плюс
    отдельный запрос за идентификатором пайплайна, который у каждого аккаунта
    свой. Писать это «по памяти», не сверив с живой документацией и не имея
    ключа, чтобы проверить хотя бы один ответ, — значит положить в стенд код,
    который гарантированно упадёт в первый же платный прогон и спишет время
    вместо денег. Приоритет владельца — fal.ai (у клиентов есть иностранные
    карты), поэтому российские сервисы оставлены точкой расширения.

    Что нужно, чтобы дописать: ключ и секрет с fusionbrain.ai — по ним видно
    актуальные адреса и идентификатор пайплайна, дальше это ~40 строк по образцу
    gen_fal (положить задачу → опрашивать статус → забрать base64-картинку).
    """
    raise NotConnected(
        "Fusion Brain (Kandinsky) не подключён: формат запроса не сверен с живой "
        "документацией, а угадывать его нельзя — платный прогон упадёт. "
        "Нужны ключ и секрет с fusionbrain.ai, после этого генератор дописывается "
        "по образцу fal.ai. Оплата у сервиса рублями и российской картой.")


def gen_yandexart(model, query, dest_base: Path, timeout=600) -> Path:
    """YandexART — ЗАГЛУШКА, генератор не написан.

    Почему не подключён. Здесь мешает не формат, а обвязка: чтобы вообще дойти
    до первой картинки, нужен платёжный аккаунт в Яндекс Облаке, сервисный
    аккаунт с ролью на генерацию, выпущенный ему ключ и идентификатор каталога —
    четыре сущности, ни одну из которых нельзя проверить без доступа. Адрес
    асинхронной генерации и формат операции без такой проверки я подтвердить не
    могу, а неподтверждённый код в стенде о деньгах — хуже честной заглушки.

    Что нужно, чтобы дописать: доступ в консоль Яндекс Облака (ключ + каталог).
    Видеогенерации через API у Яндекса на сегодня не подтверждено — считаем, что
    это только картинки.
    """
    raise NotConnected(
        "YandexART не подключён: нужен платёжный аккаунт Яндекс Облака, "
        "сервисный аккаунт с правом на генерацию, его API-ключ и идентификатор "
        "каталога. Без доступа формат не проверить, а угадывать в платном коде "
        "нельзя. Оплата у сервиса рублями и российской картой.")


#: Развилка провайдеров — ровно тот же приём, что PROVIDERS в stock.py.
#: Новый сервис = одна функция выше + одна строка здесь.
PROVIDERS = {
    "fal": gen_fal,
    "pixabay": gen_pixabay,
    "fusionbrain": gen_fusionbrain,
    "yandexart": gen_yandexart,
}


# ── ПРАЙС И МОДЕЛИ ───────────────────────────────────────────────────────────
# `цена` = (единица тарификации, цена за единицу в USD, сколько единиц в одной
# генерации при НАШИХ параметрах). Считать так, а не одним числом, обязательно:
# у видео цена за секунду, и «подорожало» от смены длительности — самая частая
# причина, по которой смета расходится с фактическим счётом.
#
# `сек` — ожидаемое время одной генерации по документации сервиса. Это ПРОГНОЗ
# для ночной оценки, пока стенд не померил реальное время; после платного
# прогона отчёт считает медиану по факту и говорит об этом явно.

def _mp(w, h):
    """Мегапиксели с округлением ВВЕРХ до целого: fal тарифицирует картинки
    по мегапикселям, и неполный мегапиксель всё равно считается за целый.
    Округлять вниз в смете о деньгах нельзя — счёт придёт больше сметы."""
    return float(max(1, math.ceil(w * h / 1_000_000.0)))


#: Наш формат — вертикаль 9:16 под Reels. Все модели просим об одном и том же,
#: иначе сравнение будет про разрешение, а не про качество.
W, H, SEC = 720, 1280, 5

MODELS = {
    # ── точка отсчёта ────────────────────────────────────────────────────────
    "pixabay-видео": {
        "provider": "pixabay", "kind": "video", "endpoint": "pixabay/videos",
        "params": {}, "цена": ("клип", 0.0, 1), "сек": 3,
        "что": "нынешний сток, чем перебивки набираются сейчас. Точка отсчёта.",
    },
    "pixabay-фото": {
        "provider": "pixabay", "kind": "image", "endpoint": "pixabay/photos",
        "params": {}, "цена": ("кадр", 0.0, 1), "сек": 2,
        "что": "нынешний сток, фото. Точка отсчёта для картиночных моделей.",
    },
    # ── картинки, fal.ai ─────────────────────────────────────────────────────
    "flux-schnell": {
        # Сверено 23.08.2026: «Your request will cost $0.003 per megapixel.
        # Images are billed by rounding up to the nearest megapixel.»
        "provider": "fal", "kind": "image", "endpoint": "fal-ai/flux/schnell",
        "params": {"image_size": {"width": W, "height": H}, "num_images": 1,
                   "num_inference_steps": 4, "output_format": "jpeg"},
        "цена": ("Мп", 0.003, _mp(W, H)), "сек": 5,
        "что": "самая дешёвая картинка. Если её качества хватает — платить больше незачем.",
    },
    "flux-dev": {
        # Сверено 23.08.2026: «Your request will cost $0.025 per megapixel.»
        "provider": "fal", "kind": "image", "endpoint": "fal-ai/flux/dev",
        "params": {"image_size": {"width": W, "height": H}, "num_images": 1,
                   "num_inference_steps": 28, "output_format": "jpeg"},
        "цена": ("Мп", 0.025, _mp(W, H)), "сек": 15,
        "что": "дороже schnell в 8 раз. Проверяем, видна ли разница глазами.",
    },
    # ── видео, fal.ai ────────────────────────────────────────────────────────
    "ltx-video": {
        # Сверено 23.08.2026: «Your request will cost $0.02 per video» —
        # тариф за клип целиком, длительность на цену не влияет.
        "provider": "fal", "kind": "video", "endpoint": "fal-ai/ltx-video",
        "params": {"negative_prompt": "worst quality, blurry, distorted, watermark, text"},
        "цена": ("клип", 0.02, 1), "сек": 30,
        "что": "самое дешёвое видео: цена за клип целиком, а не за секунду.",
    },
    "kling-standard": {
        # Сверено 23.08.2026: «Your request will cost $0.045 per second.»
        "provider": "fal", "kind": "video",
        "endpoint": "fal-ai/kling-video/v1/standard/text-to-video",
        "params": {"duration": str(SEC), "aspect_ratio": "9:16",
                   "negative_prompt": "blur, distort, low quality"},
        "цена": ("с видео", 0.045, SEC), "сек": 180,
        "что": "рабочая лошадка по цене/качеству, есть вертикаль 9:16.",
    },
    "hailuo-02-standard": {
        # Сверено 23.08.2026: «$0.045/sec. A single 6 second video will cost
        # $0.27/video» — клип у модели шестисекундный, отсюда 6 единиц.
        "provider": "fal", "kind": "video",
        "endpoint": "fal-ai/minimax/hailuo-02/standard/text-to-video",
        "params": {"prompt_optimizer": True},
        "цена": ("с видео", 0.045, 6), "сек": 180,
        "что": "конкурент Kling в той же цене. Клип 6 с, отсюда и цена выше.",
    },
    "wan-2.2": {
        # ИСПРАВЛЕНО 23.08.2026: было $0.06/с — «середина вилки». Середину брать
        # нельзя: мы просим 720p, а на 720p тариф верхний. Дословно со страницы:
        # «$0.08 per video second for 720p, $0.06 … 580p, $0.04 … 480p. Video
        # seconds are calculated at 16 frames per second.» Прежняя цифра занижала
        # смету в 1,33 раза — на столько же дырявила потолок.
        "provider": "fal", "kind": "video",
        "endpoint": "fal-ai/wan/v2.2-a14b/text-to-video",
        "params": {"resolution": "720p", "aspect_ratio": "9:16"},
        "цена": ("с видео", 0.08, SEC), "сек": 150,
        "что": "цена зависит от разрешения; мы просим 720p — это верхний тариф $0.08/с.",
    },
    "veo3-fast": {
        # ИСПРАВЛЕНО 23.08.2026: было $0.10/с. На странице модели ДВЕ разные
        # цены сразу: виджет расчёта говорит «you will be charged $0.10 (audio
        # off) or $0.15 (audio on)», а текст рядом — «$0.25 per second (audio
        # off), $0.40 (audio on)». Какая из них живая, со стороны не определить.
        # В смете о деньгах берём БОЛЬШУЮ: занижение прайса — это дыра в
        # потолке (спишут больше, чем стенд насчитал), завышение — только
        # преждевременная остановка, то есть ошибка в безопасную сторону.
        "provider": "fal", "kind": "video", "endpoint": "fal-ai/veo3/fast",
        "params": {"aspect_ratio": "9:16", "duration": "8s", "generate_audio": False},
        "цена": ("с видео", 0.25, 8), "сек": 120,
        "что": "верхняя планка качества и цены: $2 за клип 8 с. Дороже всех в прогоне.",
    },
    # ── российские сервисы: заглушки, см. комментарии у функций ──────────────
    "kandinsky": {
        "provider": "fusionbrain", "kind": "image", "endpoint": "fusionbrain/kandinsky",
        "params": {}, "цена": ("кадр", 0.0, 1), "сек": 30,
        "что": "ЗАГЛУШКА. Рубли и российская карта. Генератор не написан — нет ключа для сверки.",
    },
    "yandexart": {
        "provider": "yandexart", "kind": "image", "endpoint": "yandex/yandex-art",
        "params": {}, "цена": ("кадр", 0.0, 1), "сек": 30,
        "что": "ЗАГЛУШКА. Рубли и российская карта. Генератор не написан — нет доступа в облако.",
    },
}

#: Точки отсчёта: в прогон подмешиваются всегда, чтобы в контрольном листе было
#: с чем сравнивать. Платы за них нет.
BASELINES = {"video": "pixabay-видео", "image": "pixabay-фото"}

#: Заглушки перечислены явно: смету по ним считать нельзя (цена неизвестна),
#: и в прогон по умолчанию они не попадают.
STUBS = {name for name, m in MODELS.items()
         if PROVIDERS[m["provider"]] in (gen_fusionbrain, gen_yandexart)}


def price_of(name, models=None) -> float:
    """Цена ОДНОЙ генерации в долларах при наших параметрах."""
    m = (models or MODELS)[name]
    _, за_единицу, единиц = m["цена"]
    return round(за_единицу * единиц, 4)


def price_text(name, models=None) -> str:
    m = (models or MODELS)[name]
    единица, за_единицу, единиц = m["цена"]
    if за_единицу == 0:
        return "бесплатно"
    return "$%.4f ($%g × %g %s)" % (price_of(name, models), за_единицу, единиц, единица)


# ── ЗАПРОСЫ ИЗ НАШИХ РОЛИКОВ ─────────────────────────────────────────────────
#: Где живут смысловые блоки с visual_query. out_talk — разобранные ролики
#: (стадия talk_segment), content_plan — слоты контент-плана. Сканируем оба:
#: сравнивать надо на том, что реально приходит в завод, а не на выдуманных
#: «красивый закат» — на выдуманных все модели выглядят одинаково хорошо.
QUERY_ROOTS = ("out_talk", "content_plan", "out_reels")


def _walk_visual_queries(node, out: list):
    """Достаём visual_query с любой глубины: у out_talk это blocks[], у слота
    контент-плана — slot["blocks"][], завтра будет третья форма. Обход по
    структуре, а не по known-путям, переживает все три."""
    if isinstance(node, dict):
        q = node.get("visual_query")
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
        for v in node.values():
            _walk_visual_queries(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_visual_queries(v, out)


def collect_queries(n=10, root=None, roots=QUERY_ROOTS):
    """N настоящих visual_query из наших роликов + список файлов-источников.

    Дубли выкидываются с сохранением порядка: один и тот же запрос в двух
    роликах — это один запрос, платить за него дважды незачем.
    """
    root = Path(root or HERE)
    queries, sources = [], []
    seen = set()
    for sub in roots:
        d = root / sub
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:                                # noqa: BLE001
                continue                                     # битый json — не наша беда
            found = []
            _walk_visual_queries(data, found)
            new = [q for q in found if q.lower() not in seen]
            if not new:
                continue
            sources.append(str(path.relative_to(root)))
            for q in new:
                seen.add(q.lower())
                queries.append(q)
    return queries[:n], sources


# ── КЭШ: ВТОРОЙ РАЗ ЗА ТО ЖЕ НЕ ПЛАТИМ ───────────────────────────────────────
def cache_tag(name, query, models=None) -> str:
    """Отпечаток «модель + запрос + параметры». Параметры в отпечатке не для
    красоты: смена длительности клипа меняет и цену, и результат — старый файл
    выдавать за новый нельзя."""
    m = (models or MODELS).get(name, {})
    raw = "%s|%s|%s" % (name, query.strip().lower(),
                        json.dumps(m.get("params", {}), sort_keys=True, ensure_ascii=False))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_lookup(name, query, cache_dir=None, models=None):
    """Уже сгенерированный файл или None. Ищем по отпечатку, расширение любое."""
    d = Path(cache_dir or CACHE_DIR)
    if not d.is_dir():
        return None
    tag = cache_tag(name, query, models)
    hits = sorted(d.glob("%s_%s.*" % (_safe(name), tag)))
    hits = [h for h in hits if h.suffix != ".json" and h.stat().st_size > 0]
    return hits[0] if hits else None


def cache_base(name, query, cache_dir=None, models=None) -> Path:
    d = Path(cache_dir or CACHE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / ("%s_%s" % (_safe(name), cache_tag(name, query, models)))


def _safe(s):
    return re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "-", s).strip("-") or "x"


# ── СМЕТА ────────────────────────────────────────────────────────────────────
def plan_jobs(queries, names, models=None, cache_dir=None, use_cache=True):
    """Список работ: что, чем, почём и не лежит ли это уже в кэше."""
    models = models or MODELS
    jobs = []
    for q in queries:
        for name in names:
            hit = cache_lookup(name, q, cache_dir, models) if use_cache else None
            jobs.append({
                "модель": name,
                "запрос": q,
                "вид": models[name]["kind"],
                "из_кэша": bool(hit),
                "файл": str(hit) if hit else "",
                "цена": 0.0 if hit else price_of(name, models),
                "заглушка": name in STUBS,
            })
    return jobs


def forecast(jobs) -> float:
    """Сколько ДЕЙСТВИТЕЛЬНО спишется: кэш и точки отсчёта стоят ноль."""
    return round(sum(j["цена"] for j in jobs if not j["из_кэша"]), 4)


def plan_table(jobs, models=None):
    """Смета по моделям: штук, из кэша, к оплате."""
    models = models or MODELS
    rows = {}
    for j in jobs:
        r = rows.setdefault(j["модель"], {"всего": 0, "кэш": 0, "к_оплате": 0.0})
        r["всего"] += 1
        if j["из_кэша"]:
            r["кэш"] += 1
        else:
            r["к_оплате"] += j["цена"]
    for name, r in rows.items():
        r["к_оплате"] = round(r["к_оплате"], 4)
        r["вид"] = models[name]["kind"]
        r["цена_за_штуку"] = price_text(name, models)
    return rows


# ── ЖУРНАЛ ───────────────────────────────────────────────────────────────────
def write_journal(record, out_dir=None):
    d = Path(out_dir or OUT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_journal(out_dir=None):
    p = Path(out_dir or OUT_DIR) / JOURNAL
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:                                # noqa: BLE001
                pass
    return out


# ── ПРОГОН ───────────────────────────────────────────────────────────────────
#: Статусы, за которые денег не списано. «нет ключа» отделён от «отказа»
#: нарочно: это не поломка сервиса, а незаполненная строка в .env, и лечится
#: она человеком за минуту.
ОТКАЗЫ = ("отказ", "нет ключа")


def spent_before(out_dir=None) -> float:
    """Сколько стенд уже списал ПРОШЛЫМИ прогонами — по журналу.

    Отдельного файла-счётчика нет намеренно: журнал и так пишет цену каждой
    удачной генерации, а два источника правды про деньги разъедутся в первый же
    сбой. Считаем только статус «ок»: кэш стоит ноль, за отказы денег не берут.
    """
    total = 0.0
    for r in read_journal(out_dir):
        if r.get("статус") == "ок":
            try:
                total += float(r.get("цена_usd") or 0.0)
            except (TypeError, ValueError):
                continue
    return round(total, 6)


class Budget:
    """Потолок трат. Резервируем ДО запроса, возвращаем при отказе.

    ПОТОЛОК СКВОЗНОЙ. `before` — сколько стенд уже потратил в прошлых прогонах
    (из журнала). Раньше объект бюджета создавался пустым внутри каждого
    запуска, и `--max-usd 2`, запущенный пять раз, разрешал потратить десять
    долларов — молча, потому что каждый запуск считал себя первым. Теперь
    потолок ограничивает СУММУ за всё время: `before + spent <= limit`.

    Возврат при отказе — не мелочь: fal берёт деньги только за успешные
    результаты, и если считать отказы потраченными, стенд остановится,
    «истратив» деньги, которых никто не списывал. Резерв до запроса — тоже не
    мелочь: работы идут параллельно, и без резерва четыре потока одновременно
    проскочат последний доллар.
    """

    def __init__(self, limit, before=0.0):
        self.limit = float(limit)
        self.before = round(float(before or 0.0), 6)   # прошлые прогоны
        self.spent = 0.0                               # этот прогон
        self.stopped = False
        self._lock = threading.Lock()

    @property
    def total(self) -> float:
        """Всё, что стенд потратил за свою жизнь, включая этот прогон."""
        return round(self.before + self.spent, 6)

    @property
    def left(self) -> float:
        """Сколько ещё можно потратить, пока не упрёмся в потолок."""
        return round(max(0.0, self.limit - self.total), 6)

    def reserve(self, price) -> bool:
        with self._lock:
            if self.stopped:
                return False
            if self.before + self.spent + price > self.limit + 1e-9:
                self.stopped = True
                return False
            self.spent = round(self.spent + price, 6)
            return True

    def refund(self, price):
        with self._lock:
            self.spent = round(max(0.0, self.spent - price), 6)


def run_jobs(jobs, out_dir=None, cache_dir=None, max_usd=5.0, workers=3,
             models=None, providers=None, timeout=600, before=None, env=None):
    """Выполнить работы. Возвращает записи журнала (и пишет их на диск).

    Каждая запись — модель, запрос, цена, время, файл, статус. Статусы:
    «кэш» (не платили), «ок», «отказ» (сервис), «нет ключа», «стоп по потолку».

    `before` — сколько уже потрачено прошлыми прогонами; по умолчанию берётся
    из журнала в `out_dir`, то есть потолок сквозной сам по себе, без флагов.
    """
    models = models or MODELS
    providers = providers or PROVIDERS
    out = Path(out_dir or OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    budget = Budget(max_usd, spent_before(out) if before is None else before)
    records = []
    lock = threading.Lock()

    # КЛЮЧИ ПРОВЕРЯЕМ ДО ПРОГОНА, ОДИН РАЗ. Без ключа генератор звать незачем:
    # сети он всё равно не увидит, а полсотни одинаковых отказов в журнале
    # выглядят как поломка сервиса, хотя не хватает одной строки в .env.
    # Заодно это гарантия для тестов: нет ключа — ни одного вызова наружу.
    env = load_env() if env is None else env
    без_ключа = {}
    for prov in sorted({models[j["модель"]]["provider"]
                        for j in jobs if not j["из_кэша"] and j["модель"] in models}):
        if prov not in KEYS:          # чужой провайдер (например, тестовый) — не наше дело
            continue
        try:
            key_for(prov, env)
        except NoKey as ex:
            без_ключа[prov] = str(ex)

    def one(job):
        name, query = job["модель"], job["запрос"]
        rec = {"время": datetime.now().isoformat(timespec="seconds"),
               "модель": name, "запрос": query, "вид": job["вид"],
               "цена_usd": 0.0, "секунд": 0.0, "файл": "", "статус": "", "ошибка": ""}
        if job["из_кэша"]:
            rec.update(статус="кэш", файл=job["файл"])
            return rec

        провайдер = models[name]["provider"]
        if провайдер in без_ключа:
            rec.update(статус="нет ключа", ошибка=без_ключа[провайдер])
            return rec

        price = price_of(name, models)
        if not budget.reserve(price):
            rec.update(статус="стоп по потолку",
                       ошибка="сквозной потолок $%.2f исчерпан (в прошлых прогонах "
                              "$%.4f), эта работа не запускалась"
                              % (budget.limit, budget.before))
            return rec

        started = time.time()
        try:
            dest = cache_base(name, query, cache_dir, models)
            path = providers[models[name]["provider"]](models[name], query, dest, timeout)
            rec.update(статус="ок", файл=str(path), цена_usd=price,
                       секунд=round(time.time() - started, 1))
        except (NoKey, NotConnected, GenFailed) as ex:
            budget.refund(price)                 # за отказ денег не берут
            rec.update(статус="отказ", ошибка=str(ex),
                       секунд=round(time.time() - started, 1))
        except Exception as ex:                              # noqa: BLE001
            budget.refund(price)
            rec.update(статус="отказ", ошибка="%s: %s" % (type(ex).__name__, ex),
                       секунд=round(time.time() - started, 1))
        return rec

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for rec in pool.map(one, jobs):
            with lock:
                records.append(rec)
                write_journal(rec, out)
    return records, budget


def summarize(records, models=None):
    """Итог: модель → потрачено, сколько секунд на клип, сколько отказов."""
    models = models or MODELS
    rows = {}
    for r in records:
        row = rows.setdefault(r["модель"], {
            "потрачено": 0.0, "сделано": 0, "кэш": 0, "отказов": 0, "времена": []})
        row["потрачено"] = round(row["потрачено"] + r.get("цена_usd", 0.0), 4)
        st = r.get("статус")
        if st == "ок":
            row["сделано"] += 1
            if r.get("секунд") is not None:      # ноль секунд — тоже замер
                row["времена"].append(r["секунд"])
        elif st == "кэш":
            row["кэш"] += 1
        elif st in ОТКАЗЫ:
            row["отказов"] += 1
    for name, row in rows.items():
        row["секунд_на_клип"] = (round(statistics.median(row["времена"]), 1)
                                 if row["времена"] else None)
        row["прогноз_секунд"] = models.get(name, {}).get("сек")
    return rows


def night_estimate(rows, reels=30, clips_per_reel=5, workers=3, night_hours=8,
                   models=None):
    """Влезает ли прогон на 30 роликов в ночь.

    Считаем по каждой модели отдельно: «если всю ночную партию делать ЭТОЙ
    моделью». Если реальное время уже померено — берём медиану замера, иначе
    прогноз из документации и честно говорим, что это прогноз.
    """
    models = models or MODELS
    total = reels * clips_per_reel
    lines = []
    for name in sorted(rows):
        row = rows[name]
        sec = row.get("секунд_на_клип")
        источник = "замер"
        if not sec:
            sec = row.get("прогноз_секунд") or models.get(name, {}).get("сек")
            источник = "прогноз по документации"
        if not sec:
            continue
        часов = total * sec / max(1, workers) / 3600.0
        цена = round(total * price_of(name, models), 2) if name in models else 0.0
        lines.append({
            "модель": name, "секунд_на_клип": sec, "источник": источник,
            "часов": round(часов, 1), "влезает": часов <= night_hours,
            "цена_за_ночь": цена, "генераций": total,
        })
    return lines


# ── КОНТРОЛЬНЫЙ ЛИСТ ─────────────────────────────────────────────────────────
def _строки_и_столбцы(records, models=None):
    """Порядок для обоих отчётов: строки — наши запросы, столбцы — модели.

    Сток идёт ПЕРВЫМ столбцом всегда и без вариантов: сравнение отвечает на
    вопрос «стоит ли платить вместо нынешнего бесплатного», а не «какая модель
    красивее». Порядок внутри — как в прогоне, чтобы лист и страница читались
    одинаково.
    """
    models = models or MODELS
    queries, names = [], []
    for r in records:
        if r["запрос"] not in queries:
            queries.append(r["запрос"])
        if r["модель"] not in names:
            names.append(r["модель"])
    baseline = [n for n in names if models.get(n, {}).get("provider") == "pixabay"]
    return queries, baseline + [n for n in names if n not in baseline]


def _font(size, bold=False):
    """Шрифт завода с кириллицей; если файла нет — встроенный PIL, лист всё
    равно соберётся (только некрасиво). Падать из-за шрифта стенд не должен."""
    from PIL import ImageFont
    path = HERE / "fonts" / "Inter" / "Inter-Variable.ttf"
    try:
        ft = ImageFont.truetype(str(path), size)
        try:
            ft.set_variation_by_axes([700 if bold else 400])
        except Exception:                                    # noqa: BLE001
            pass
        return ft
    except Exception:                                        # noqa: BLE001
        return ImageFont.load_default()


def preview_image(path, kind, size=(240, 426)):
    """Превью для листа: картинка как есть, из клипа — кадр на PREVIEW_AT.

    Кадр тянем ffmpeg завода (imageio_ffmpeg), а не cv2: cv2 не открывает часть
    выходных h264 — на этом уже обжигались в checks.py.
    """
    from PIL import Image
    p = Path(path)
    if not p.exists():
        return None
    try:
        if kind == "video":
            import tempfile
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            with tempfile.TemporaryDirectory() as td:
                frame = os.path.join(td, "f.png")
                r = subprocess.run(
                    [ff, "-y", "-loglevel", "error", "-ss", "%.2f" % PREVIEW_AT,
                     "-i", str(p), "-frames:v", "1", frame],
                    capture_output=True)
                if r.returncode != 0 or not os.path.exists(frame):
                    r = subprocess.run(                       # клип короче PREVIEW_AT
                        [ff, "-y", "-loglevel", "error", "-i", str(p),
                         "-frames:v", "1", frame], capture_output=True)
                if r.returncode != 0 or not os.path.exists(frame):
                    return None
                im = Image.open(frame)
                im.load()
        else:
            im = Image.open(p)
            im.load()
    except Exception:                                        # noqa: BLE001
        return None
    im = im.convert("RGB")
    im.thumbnail(size, Image.LANCZOS)
    return im


def build_sheet(records, out_path=None, models=None, cell=(240, 426)):
    """Один лист: строка — наш запрос, столбцы — модели, первым Pixabay.

    Смысл листа — выбор глазами. Поэтому под каждым кадром подписано, чья это
    модель и сколько СТОИЛА эта конкретная штука: «красивее» без «дороже в 30
    раз» — не решение, а соблазн.
    """
    from PIL import Image, ImageDraw
    models = models or MODELS
    ok = [r for r in records if r.get("файл") and r.get("статус") in ("ок", "кэш")]
    if not ok:
        return None

    queries, names = _строки_и_столбцы(records, models)
    by = {(r["модель"], r["запрос"]): r for r in records}
    cw, ch = cell
    pad, head, label, left = 10, 54, 42, 240
    W_ = left + len(names) * (cw + pad) + pad
    H_ = head + len(queries) * (ch + label + pad) + pad
    sheet = Image.new("RGB", (W_, H_), (246, 245, 243))
    d = ImageDraw.Draw(sheet)
    f_head = _font(20, bold=True)
    f_small = _font(13)
    f_q = _font(15)

    d.text((pad, 12), "СРАВНЕНИЕ ГЕНЕРАТОРОВ — один запрос, разные модели. "
                      "Первый столбец: что отдаёт нынешний сток.",
           font=f_head, fill=(20, 20, 20))
    for ci, name in enumerate(names):
        x = left + ci * (cw + pad)
        d.text((x, head - 24), "%s · %s" % (name, price_text(name, models)),
               font=f_small, fill=(40, 40, 40))

    for ri, q in enumerate(queries):
        y = head + ri * (ch + label + pad)
        _wrap(d, q, (pad, y + 4), left - 2 * pad, f_q, (30, 30, 30))
        for ci, name in enumerate(names):
            x = left + ci * (cw + pad)
            d.rectangle([x, y, x + cw, y + ch], fill=(228, 226, 223))
            rec = by.get((name, q))
            im = preview_image(rec["файл"], rec.get("вид", "image"), cell) if (
                rec and rec.get("файл")) else None
            if im is not None:
                sheet.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
                подпись = "$%.4f · %.0fс" % (rec.get("цена_usd", 0.0),
                                             rec.get("секунд", 0) or 0)
                if rec.get("статус") == "кэш":
                    подпись = "из кэша, $0"
            else:
                причина = (rec or {}).get("ошибка", "не генерировалось")
                _wrap(d, причина, (x + 6, y + 8), cw - 12, f_small, (110, 60, 60), 6)
                подпись = (rec or {}).get("статус", "—")
            d.text((x, y + ch + 6), name, font=f_small, fill=(20, 20, 20))
            d.text((x, y + ch + 22), подпись, font=f_small, fill=(110, 106, 102))

    out_path = Path(out_path or (OUT_DIR / SHEET))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90)
    return out_path


def _wrap(d, text, xy, width, font, fill, max_lines=4):
    """Перенос по словам: запросы и причины отказа длинные, а колонка узкая."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        probe = (cur + " " + w).strip()
        if d.textlength(probe, font=font) <= width or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    x, y = xy
    for i, line in enumerate(lines):
        d.text((x, y + i * (font.size + 3)), line, font=font, fill=fill)


# ── СТРАНИЦА СРАВНЕНИЯ (ГЛАВНЫЙ ФАЙЛ ДЛЯ ЧЕЛОВЕКА) ───────────────────────────
# Смотреть будет владелец, а не программист: он не откроет журнал, не запустит
# проигрыватель по десяти путям и не станет сопоставлять цену с файлом. Поэтому
# всё в одном html: сток и генерация рядом, клипы играют прямо в странице,
# под каждым — цена именно этой штуки. Ни одной внешней ссылки: страницу можно
# переслать, унести на флешке и открыть без интернета.

_MIME = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp"}


def _вшить(path, limit_mb=MAX_EMBED_MB):
    """Файл → data:-строка. None, если файла нет или он слишком тяжёлый."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    if p.stat().st_size > limit_mb * 1024 * 1024:
        return None
    mime = _MIME.get(p.suffix.lower())
    if not mime:
        return None
    return "data:%s;base64,%s" % (
        mime, base64.b64encode(p.read_bytes()).decode("ascii"))


def _вшить_кадр(path, kind, size=(420, 746)):
    """Кадр-обложка для клипа, тоже строкой внутри страницы."""
    im = preview_image(path, kind, size)
    if im is None:
        return None
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64,%s" % base64.b64encode(buf.getvalue()).decode("ascii")


def _деньги(usd) -> str:
    """«$0.2250 · 19 ₽» — доллары точно, рубли справочно."""
    usd = float(usd or 0.0)
    if usd <= 0:
        return "бесплатно"
    return "$%.4f · %.0f ₽" % (usd, rub(usd))


def build_page(records, out_path=None, models=None, budget=None, embed=True):
    """Страница сравнения: сток и генерация рядом, с ценой каждого клипа.

    Возвращает путь или None, если показывать нечего. `embed=False` — режим для
    тестов и очень больших прогонов: страница собирается без вшитых файлов.
    """
    models = models or MODELS
    ok = [r for r in records if r.get("файл") and r.get("статус") in ("ок", "кэш")]
    if not ok:
        return None
    queries, names = _строки_и_столбцы(records, models)
    by = {(r["модель"], r["запрос"]): r for r in records}
    итог = summarize(records, models)

    e = html.escape
    ч = []
    ч.append("<!doctype html>")
    ч.append('<html lang="ru"><head><meta charset="utf-8">')
    ч.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    ч.append("<title>Сравнение: нынешний сток и генерация</title>")
    ч.append("<style>%s</style></head><body>" % _СТИЛЬ)

    ч.append("<h1>Сток или генерация — смотрим на одних и тех же запросах</h1>")
    ч.append('<p class="лид">Слева в каждой строке — <b>то, что завод берёт '
             'сейчас</b> (бесплатный сток Pixabay). Правее — то, что на тот же '
             'самый запрос выдали платные модели. Запросы настоящие, из наших '
             'разобранных роликов, ни один под модель не переписывался. '
             'Клипы играют прямо здесь: нажмите на кадр.</p>')

    ч.append('<div class="итоги"><table><tr><th>модель</th><th>цена за штуку</th>'
             '<th>сделано</th><th>из кэша</th><th>не вышло</th>'
             '<th>потрачено</th></tr>')
    всего = 0.0
    for name in names:
        r = итог.get(name, {})
        потрачено = float(r.get("потрачено") or 0.0)
        всего += потрачено
        ч.append("<tr><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td>"
                 "<td>%s</td></tr>" % (
                     e(name), e(price_text(name, models)) if name in models else "—",
                     r.get("сделано", 0), r.get("кэш", 0), r.get("отказов", 0),
                     e(_деньги(потрачено))))
    ч.append('<tr class="всего"><td>ВСЕГО ЗА ЭТОТ ПРОГОН</td><td></td><td></td>'
             '<td></td><td></td><td>%s</td></tr>' % e(_деньги(всего)))
    ч.append("</table>")
    if budget is not None:
        ч.append('<p class="сноска">Потолок сквозной: $%.2f на все прогоны '
                 'вместе. За всё время стенд списал %s%s.</p>' % (
                     budget.limit, e(_деньги(budget.total)),
                     ", потолок сработал и часть работ не запускалась"
                     if budget.stopped else ""))
    ч.append('<p class="сноска">Прайс сверялся %s по страницам самих моделей на '
             'fal.ai. Рубли — по курсу ЦБ %.4f ₽/$ на ту же дату, справочно: '
             'счёт всё равно в долларах.</p>' % (e(PRICES_CHECKED), RUB_PER_USD))
    ч.append("</div>")

    ч.append('<div class="как"><b>На что смотреть, по каждой строке:</b>'
             '<ol><li><b>Попадание в смысл.</b> Кадр про то, о чём фраза, — или '
             'просто красивый? Сток на любой запрос про работу даёт «женщину у '
             'ноутбука»; генерация должна выигрывать именно здесь, иначе платить '
             'не за что.</li>'
             '<li><b>Не выглядит ли чужим.</b> Стоковый кадр узнаётся как '
             'стоковый. Уходит ли это ощущение.</li>'
             '<li><b>Брак.</b> Руки, лица, текст в кадре, дёрганое движение. '
             'Сколько клипов пришлось бы выкинуть — столько же процентов сверху '
             'к цене.</li></ol>'
             'Вердикт нужен одной фразой: «разница есть, и я готов за неё '
             'платить — да/нет». «Нет» — нормальный исход, он закрывает затею '
             'и экономит деньги.</div>')

    for q in queries:
        ч.append('<section class="ряд"><h2>%s</h2><div class="клетки">' % e(q))
        for name in names:
            rec = by.get((name, q))
            ч.append(_клетка(name, rec, models, embed))
        ч.append("</div></section>")

    ч.append('<footer>Собрано стендом bench_generate.py %s. Файл '
             'самодостаточный: клипы лежат внутри него, интернет для просмотра '
             'не нужен.</footer>' % datetime.now().strftime("%d.%m.%Y %H:%M"))
    ч.append("</body></html>")

    out_path = Path(out_path or (OUT_DIR / PAGE))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(ч), encoding="utf-8")
    return out_path


def _клетка(name, rec, models, embed=True) -> str:
    """Одна ячейка страницы: кадр или клип, имя модели, цена именно этой штуки."""
    e = html.escape
    подпись = e((models.get(name) or {}).get("что", ""))
    заголовок = '<div class="имя">%s</div>' % e(name)
    if not rec:
        return ('<figure class="клетка"><div class="пусто">не '
                'генерировалось</div><figcaption>%s</figcaption></figure>' % заголовок)

    статус = rec.get("статус", "")
    if статус in ("ок", "кэш") and rec.get("файл"):
        вид = rec.get("вид", "image")
        источник = _вшить(rec["файл"]) if embed else None
        if вид == "video":
            кадр = _вшить_кадр(rec["файл"], вид) if embed else None
            if источник:
                тело = ('<video controls loop muted playsinline preload="metadata"'
                        '%s src="%s"></video>'
                        % ((' poster="%s"' % кадр) if кадр else "", источник))
            elif кадр:
                тело = ('<img src="%s" alt=""><div class="тяжёлый">клип не '
                        'поместился в страницу — лежит файлом: %s</div>'
                        % (кадр, e(rec["файл"])))
            else:
                тело = ('<div class="пусто">файл на месте, но кадр не вынулся: '
                        '%s</div>' % e(rec["файл"]))
        else:
            тело = ('<img src="%s" alt="">' % источник if источник
                    else '<div class="пусто">%s</div>' % e(rec["файл"]))
        цена = (_деньги(rec.get("цена_usd", 0.0)) if статус == "ок"
                else "из кэша, $0 — платили в прошлый прогон")
        # Время показываем только у того, что делалось СЕЙЧАС: приписать
        # «ждали 174 с» к файлу из кэша — значит соврать, он взялся мгновенно.
        сек = (rec.get("секунд") or 0) if статус == "ок" else 0
        время = (" · %.0f с ждали" % сек) if сек else ""
        return ('<figure class="клетка">%s<figcaption>%s'
                '<div class="цена">%s%s</div><div class="что">%s</div>'
                '</figcaption></figure>'
                % (тело, заголовок, e(цена), время, подпись))

    причина = e(rec.get("ошибка") or статус or "не вышло")
    return ('<figure class="клетка отказ"><div class="пусто">%s<br><span>%s</span>'
            '</div><figcaption>%s<div class="цена">денег не списано</div>'
            '<div class="что">%s</div></figcaption></figure>'
            % (e(статус or "не вышло"), причина, заголовок, подпись))


#: Стиль внутри страницы: ни одного внешнего файла, шрифты — какие есть в
#: системе. Страница обязана открываться на любой машине и без интернета.
_СТИЛЬ = """
:root { color-scheme: light; }
body { margin:0; padding:28px 20px 60px; background:#f6f5f3; color:#17181a;
       font:16px/1.5 "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
h1 { font-size:26px; margin:0 0 10px; }
.лид { max-width:900px; margin:0 0 20px; font-size:17px; }
.итоги { background:#fff; border:1px solid #e2e0dc; border-radius:12px;
         padding:14px 16px; margin:0 0 18px; max-width:900px; }
.итоги table { border-collapse:collapse; width:100%; font-size:14px; }
.итоги th { text-align:left; font-weight:600; color:#666; padding:4px 10px 6px 0;
            border-bottom:1px solid #eceae6; }
.итоги td { padding:4px 10px 4px 0; border-bottom:1px solid #f2f0ec; }
.итоги tr.всего td { font-weight:700; border-bottom:none; }
.сноска { color:#6b6b6b; font-size:13px; margin:10px 0 0; }
.как { background:#fff8e6; border:1px solid #f0e2b8; border-radius:12px;
       padding:14px 18px; max-width:900px; margin:0 0 26px; font-size:15px; }
.как ol { margin:8px 0; padding-left:22px; }
.ряд { margin:0 0 30px; }
.ряд h2 { font-size:18px; margin:0 0 10px; padding:6px 12px; background:#fff;
          border:1px solid #e2e0dc; border-radius:8px; display:inline-block; }
.клетки { display:flex; gap:14px; flex-wrap:wrap; align-items:flex-start; }
.клетка { margin:0; width:240px; background:#fff; border:1px solid #e2e0dc;
          border-radius:12px; overflow:hidden; }
.клетка video, .клетка img { display:block; width:240px; height:auto;
          max-height:430px; background:#e4e2df; }
.клетка .пусто { width:240px; min-height:160px; padding:14px; box-sizing:border-box;
          background:#f0eeea; color:#8a5a5a; font-size:13px; }
.клетка .пусто span { color:#8a8a8a; }
.клетка.отказ { border-color:#e6d3d3; }
figcaption { padding:8px 10px 12px; }
.имя { font-weight:600; font-size:14px; }
.цена { font-size:13px; color:#3a3a3a; margin-top:2px; }
.что { font-size:12px; color:#7a7a7a; margin-top:4px; }
.тяжёлый { padding:8px 10px; font-size:12px; color:#8a5a5a; }
footer { color:#8a8a8a; font-size:13px; margin-top:40px; }
"""


# ── ПЕЧАТЬ ───────────────────────────────────────────────────────────────────
def _keys_report(models=None, env=None, names=None):
    """Статус ключей ТОЛЬКО у сервисов, которые участвуют в этом прогоне:
    ругаться на отсутствие ключа Яндекса, когда Яндекс в прогон не входит, —
    ровно тот шум, из-за которого перестают читать вывод."""
    models = models or MODELS
    env = load_env() if env is None else env
    участвуют = names or list(models)
    used = sorted({models[n]["provider"] for n in участвуют if n in models})
    lines = []
    for prov in used:
        try:
            key_for(prov, env)
            lines.append("  [есть]  %s" % prov)
        except NoKey as ex:
            lines.append("  [НЕТ]   %s — %s" % (prov, ex))
    return lines


def print_plan(jobs, queries, sources, max_usd, spend, models=None,
               reels=30, clips=5, workers=3, night_hours=8, уже=0.0):
    models = models or MODELS
    итого = forecast(jobs)
    print("")
    print("═" * 86)
    print("СМЕТА ПРОГОНА" + ("" if spend else "   (--dry-run: НИ ОДНОГО платного запроса не уйдёт)"))
    print("═" * 86)
    print("Запросов: %d — настоящие visual_query из наших роликов." % len(queries))
    if sources:
        print("Источники: %s" % ", ".join(sources[:5]) + (" …" if len(sources) > 5 else ""))
    for q in queries:
        print("    · %s" % q)
    print("")
    print("%-22s %-6s %-32s %5s %6s %10s" %
          ("модель", "вид", "цена за штуку", "штук", "кэш", "к оплате"))
    print("─" * 86)
    for name, r in plan_table(jobs, models).items():
        пометка = "  ← ЗАГЛУШКА" if name in STUBS else ""
        print("%-22s %-6s %-32s %5d %6d %9.4f$%s" %
              (name, "видео" if r["вид"] == "video" else "фото",
               r["цена_за_штуку"], r["всего"], r["кэш"], r["к_оплате"], пометка))
    print("─" * 86)
    # Итог печатаем ДО КОПЕЙКИ (четыре знака), а не округлённым: $0.0460 и
    # $0.05 — разные числа, и в смете о деньгах округление вверх читается как
    # «дороже, чем на самом деле», а вниз — как дыра в потолке.
    print("ИТОГО К ОПЛАТЕ: $%.4f  (примерно %.0f ₽)" % (итого, rub(итого)))
    if уже:
        print("Уже потрачено прошлыми прогонами: $%.4f  (журнал %s)"
              % (уже, OUT_DIR / JOURNAL))
        print("ВМЕСТЕ С ПРОШЛЫМИ: $%.4f     сквозной потолок --max-usd: $%.2f"
              % (уже + итого, max_usd))
    else:
        print("Прошлых трат нет.        сквозной потолок --max-usd: $%.2f" % max_usd)
    print("Потолок считает ВСЕ прогоны вместе, а не каждый заново: пять "
          "запусков подряд не превратятся в пять потолков.")
    print("Прайс сверялся %s по страницам самих моделей на fal.ai. Цены там "
          "меняются — перед крупным прогоном сверь заново." % PRICES_CHECKED)
    if уже + итого > max_usd + 1e-9:
        print("")
        print("!! НЕ ВЛЕЗАЕТ В ПОТОЛОК. Стенд не стартует. Варианты:")
        print("   · уменьшить число запросов:  -n 3")
        print("   · оставить дешёвые модели:   --models ltx-video,kling-standard")
        print("   · поднять потолок осознанно: --max-usd %.0f"
              % math.ceil(уже + итого + 0.5))
    else:
        print("Влезает.")
    # Ночная партия — прогноз ДО прогона. Смысл: увидеть заранее, что модель,
    # проходящая по деньгам, может не пройти по времени. После платного прогона
    # тот же расчёт печатается заново, но уже по замеру.
    имена = []
    for j in jobs:
        if j["модель"] not in имена:
            имена.append(j["модель"])
    прогноз = {n: {"секунд_на_клип": None, "прогноз_секунд": models[n]["сек"]}
               for n in имена}
    print("")
    print("НОЧНАЯ ПАРТИЯ — ПРОГНОЗ (%d роликов × %d клипов = %d генераций, "
          "параллельно по %d, ночь = %d ч):"
          % (reels, clips, reels * clips, workers, night_hours))
    print("Время взято из документации сервисов, это НЕ замер. Настоящее время "
          "стенд померит на платном прогоне.")
    for e in night_estimate(прогноз, reels, clips, workers, night_hours, models):
        print("  %-22s %4.0f с/клип → %5.1f ч %-11s ночь стоит $%.2f" %
              (e["модель"], e["секунд_на_клип"], e["часов"],
               "ВЛЕЗАЕТ" if e["влезает"] else "НЕ ВЛЕЗАЕТ", e["цена_за_ночь"]))

    print("")
    print("КЛЮЧИ (только те сервисы, что участвуют в этом прогоне):")
    for line in _keys_report(models, names=имена):
        print(line)
    if not spend:
        print("")
        print("Это только смета. Чтобы реально потратить — добавь --spend.")
    print("")


def print_summary(records, budget, models=None, reels=30, clips=5, workers=3,
                  night_hours=8):
    models = models or MODELS
    rows = summarize(records, models)
    print("")
    print("═" * 78)
    print("ИТОГ ПРОГОНА")
    print("═" * 78)
    print("%-22s %10s %7s %5s %8s %14s" %
          ("модель", "потрачено", "сделано", "кэш", "отказов", "секунд на клип"))
    print("─" * 78)
    for name in sorted(rows):
        r = rows[name]
        сек = "%.0f" % r["секунд_на_клип"] if r["секунд_на_клип"] else "—"
        print("%-22s %9.4f$ %7d %5d %8d %14s" %
              (name, r["потрачено"], r["сделано"], r["кэш"], r["отказов"], сек))
    print("─" * 78)
    print("Списано в этом прогоне: $%.4f  (примерно %.0f ₽)"
          % (budget.spent, rub(budget.spent)))
    if budget.before:
        print("Было списано раньше:    $%.4f" % budget.before)
    print("ВСЕГО ЗА ВСЁ ВРЕМЯ:     $%.4f из сквозного потолка $%.2f%s"
          % (budget.total, budget.limit,
             "   ← ПОТОЛОК СРАБОТАЛ, часть работ не запускалась"
             if budget.stopped else ""))

    отказы = [r for r in records if r.get("статус") in ОТКАЗЫ]
    if отказы:
        print("")
        print("ОТКАЗЫ (денег за них не списано):")
        видели = set()
        for r in отказы:
            ключ = (r["модель"], r["ошибка"][:80])
            if ключ in видели:
                continue
            видели.add(ключ)
            print("  · %s: %s" % (r["модель"], r["ошибка"]))

    print("")
    print("ВРЕМЯ И НОЧНОЙ ПРОГОН (%d роликов × %d клипов = %d генераций, "
          "параллельно по %d):" % (reels, clips, reels * clips, workers))
    for e in night_estimate(rows, reels, clips, workers, night_hours, models):
        print("  %-22s %4.0f с/клип (%s) → %5.1f ч %s  ночь стоит $%.2f" %
              (e["модель"], e["секунд_на_клип"], e["источник"], e["часов"],
               "ВЛЕЗАЕТ" if e["влезает"] else "НЕ ВЛЕЗАЕТ", e["цена_за_ночь"]))
    print("  (ночь считаем как %d ч)" % night_hours)


# ── CLI ──────────────────────────────────────────────────────────────────────
def _utf8_output():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                    # noqa: BLE001
            pass


def default_models(kind="all"):
    """По умолчанию — всё, что реально подключено (заглушки в прогон не идут)."""
    out = [n for n, m in MODELS.items()
           if n not in STUBS and (kind == "all" or m["kind"] == kind)]
    return out


def main(argv=None):
    _utf8_output()
    ap = argparse.ArgumentParser(
        description="Стенд сравнения генераторов видео и картинок. "
                    "По умолчанию только считает смету и не тратит ничего.")
    ap.add_argument("-n", type=int, default=10,
                    help="сколько наших запросов взять (по умолчанию 10)")
    ap.add_argument("--models", default="",
                    help="список моделей через запятую; по умолчанию все подключённые")
    ap.add_argument("--kind", default="all", choices=["all", "video", "image"],
                    help="только видео / только картинки")
    ap.add_argument("--max-usd", type=float, default=5.0,
                    help="жёсткий потолок трат в долларах (по умолчанию 5)")
    ap.add_argument("--spend", action="store_true",
                    help="РАЗРЕШИТЬ реальные платные запросы (без этого — только смета)")
    ap.add_argument("--jobs", type=int, default=3,
                    help="сколько генераций одновременно (по умолчанию 3)")
    ap.add_argument("--no-cache", action="store_true",
                    help="игнорировать кэш и платить заново")
    ap.add_argument("--timeout", type=int, default=600,
                    help="потолок ожидания одной генерации, секунд")
    ap.add_argument("--reels", type=int, default=30, help="роликов в ночной партии")
    ap.add_argument("--clips", type=int, default=5, help="перебивок на ролик")
    ap.add_argument("--night-hours", type=int, default=8, help="сколько часов длится ночь")
    ap.add_argument("--list-models", action="store_true",
                    help="показать прайс-лист и статус ключей и выйти")
    ap.add_argument("--sheet-only", action="store_true",
                    help="пересобрать контрольный лист по журналу, ничего не генерируя")
    a = ap.parse_args(argv)

    # Ключ стенд читает из .env, а предохранитель тестов чистит ОКРУЖЕНИЕ —
    # то есть мимо него. Под тестами оборону держала только блокировка сокетов;
    # если её однажды снимут, --spend уйдёт в сервис за настоящие деньги
    # владельца. Поэтому отказ ставим здесь, на стороне того, кто тратит.
    под_тестами = os.environ.get("KARUSEL_TESTS_SANDBOX") == "1"
    сеть_живая = globals()["_request"] is _НАСТОЯЩИЙ_ЗАПРОС
    if a.spend and под_тестами and сеть_живая:
        print("ОТКАЗ: --spend под предохранителем тестов, и сетевая функция НЕ "
              "подменена — то есть запрос ушёл бы по-настоящему, за деньги "
              "владельца. Тест, которому нужен платный путь, обязан подменить "
              "_request растяжкой: тогда отказ снимется сам. Для настоящего "
              "прогона запускай стенд напрямую, не через run_tests.py.")
        return 1

    if a.list_models:
        print("МОДЕЛИ СТЕНДА (прайс сверялся %s)\n" % PRICES_CHECKED)
        print("%-22s %-6s %-32s %s" % ("модель", "вид", "цена за штуку", "что это"))
        print("─" * 118)
        for name, m in MODELS.items():
            print("%-22s %-6s %-32s %s" % (
                name, "видео" if m["kind"] == "video" else "фото",
                price_text(name), m["что"]))
        print("\nКЛЮЧИ (берутся из файла .env рядом со стендом, "
              "по строке на ключ — руками в окружение прописывать нечего):")
        for line in _keys_report():
            print(line)
        уже = spent_before()
        if уже:
            print("\nСтенд уже потратил за всё время: $%.4f (журнал %s). "
                  "Сквозной потолок --max-usd считает эту сумму тоже."
                  % (уже, OUT_DIR / JOURNAL))
        return 0

    if a.sheet_only:
        records = read_journal()
        if not records:
            print("Журнала нет — сначала сделай прогон с --spend.")
            return 1
        страница = build_page(records)
        path = build_sheet(records)
        print("СТРАНИЦА СРАВНЕНИЯ (открыть двойным щелчком): %s"
              % (страница or "не собралась — нет ни одного файла"))
        print("Контрольный лист картинкой: %s"
              % (path or "не собрался — нет ни одного файла"))
        return 0 if страница or path else 1

    queries, sources = collect_queries(a.n)
    if not queries:
        print("НЕ НАШЁЛ НИ ОДНОГО ЗАПРОСА. Стенд берёт visual_query из разобранных "
              "роликов (%s). Разбери хотя бы один ролик — или сравнивать будет "
              "не на чем." % ", ".join(QUERY_ROOTS))
        return 1

    names = ([s.strip() for s in a.models.split(",") if s.strip()]
             or default_models(a.kind))
    неизвестные = [n for n in names if n not in MODELS]
    if неизвестные:
        print("Не знаю таких моделей: %s\nЕсть: %s"
              % (", ".join(неизвестные), ", ".join(MODELS)))
        return 1
    # Точку отсчёта подмешиваем всегда: без Pixabay в листе не с чем сравнивать.
    for вид in {MODELS[n]["kind"] for n in names}:
        base = BASELINES.get(вид)
        if base and base not in names:
            names.insert(0, base)

    jobs = plan_jobs(queries, names, use_cache=not a.no_cache)
    уже = spent_before()
    print_plan(jobs, queries, sources, a.max_usd, a.spend, reels=a.reels,
               clips=a.clips, workers=a.jobs, night_hours=a.night_hours, уже=уже)

    if not a.spend:
        return 0
    итого = forecast(jobs)
    # Потолок СКВОЗНОЙ: сравниваем с ним сумму «прошлые прогоны + этот». Иначе
    # `--max-usd 2`, запущенный пять раз, тихо разрешал бы потратить десять.
    if уже + итого > a.max_usd + 1e-9:
        print("ОСТАНОВЛЕН: ни один запрос не отправлен.")
        print("  смета этого прогона:        $%.4f" % итого)
        if уже:
            print("  уже списано прошлыми:       $%.4f" % уже)
            print("  вместе:                     $%.4f" % (уже + итого))
        print("  сквозной потолок --max-usd: $%.2f" % a.max_usd)
        print("Потолок считает ВСЕ прогоны вместе. Если тратить действительно "
              "нужно — подними его осознанно: --max-usd %.0f"
              % math.ceil(уже + итого + 0.5))
        return 2

    print("ПОШЁЛ ПЛАТНЫЙ ПРОГОН. Сквозной потолок $%.2f (из них уже списано "
          "$%.4f), параллельно по %d.\n" % (a.max_usd, уже, a.jobs))
    records, budget = run_jobs(jobs, max_usd=a.max_usd, workers=a.jobs,
                               timeout=a.timeout, before=уже)
    print_summary(records, budget, reels=a.reels, clips=a.clips,
                  workers=a.jobs, night_hours=a.night_hours)
    страница = build_page(records, budget=budget)
    path = build_sheet(records)
    print("")
    print("СМОТРЕТЬ ЗДЕСЬ (двойной щелчок, откроется в браузере):")
    print("  %s" % (страница or "страница не собралась — ни одного файла не вышло"))
    print("Тот же расклад картинкой: %s"
          % (path or "не собрался — ни одного файла не вышло"))
    print("Журнал (по строке на генерацию): %s" % (OUT_DIR / JOURNAL))
    print("Сами клипы: %s" % CACHE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
