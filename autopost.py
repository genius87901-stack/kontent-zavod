# -*- coding: utf-8 -*-
"""
АВТОПОСТИНГ КАРУСЕЛЕЙ И REELS — Upload-Post.

ПОЧЕМУ ЭТОТ СЕРВИС. Instagram Graph API не принимает байты файла: Meta требует
публичную ссылку на медиа. Это правило самой Meta, а не причуда посредника —
поэтому вопрос стоял не «как обойти», а «у кого хостинг в комплекте».
Upload-Post принимает файлы напрямую (multipart) и хостит их за нас.
Запасной вариант, если он не устроит: Postiz (open source, self-host).

ЧТО НУЖНО И ГДЕ ЛЕЖИТ (два файла, не один — так их раскладывает мастер):
    UPLOAD_POST_API_KEY   ключ из личного кабинета — СЕКРЕТ, лежит в `.env`
    UPLOAD_POST_USER      имя профиля (параметр user в API) — не секрет,
                          лежит в `config/factory.config.json`
Читаем оба через modules/publishing/config_reader.py — тем же ридером, что и
доктор модуля. Ключ в чат не присылать.

КОДЫ ВОЗВРАТА: 0 — вышло, 1 — отказ (поста нет), 3 — исход не подтверждён.
Их читает release.py, чтобы не выдавать провал за успех.

ВАЖНО ПРО ЗАГОЛОВОК АВТОРИЗАЦИИ: ключ идёт со словом Apikey —
    Authorization: Apikey <ключ>
Без префикса будет 401. (У Pexels наоборот — там ключ голый. Не перепутать.)

ТАРИФ «БАЗОВЫЙ» $24/мес, ОПЛАЧЕН 21.07.2026: публикации БЕЗЛИМИТНЫЕ, 5 профилей,
все площадки открыты, FFmpeg 300 мин/мес. Прежний счёт «10 загрузок в месяц»
больше не действует — не закладываться на него в рассуждениях.

ПРЕДОХРАНИТЕЛЬ. По умолчанию скрипт НИЧЕГО НЕ ОТПРАВЛЯЕТ: он показывает, что
именно ушло бы. Отправка — только с явным --send.
Раньше это обосновывалось лимитом тарифа; лимита больше нет, но предохранитель
остаётся, и причина стала ГЛАВНОЙ, а не второй: публикация необратима и видна
подписчикам. Удалить пост можно, развидеть — нет. Публикуем только по явному
слову Валерии в текущем окне, а не по «мы же договорились» из прошлой сессии.

ПУБЛИКАЦИЯ АСИНХРОННАЯ — 200 ЭТО ЕЩЁ НЕ ПУБЛИКАЦИЯ. Сервис отдаёт request_id и
слова про upload worker, а результат приходит позже. Раньше скрипт печатал
«ОТПРАВЛЕНО» по коду 200 и врал: на #5 обработка шла ~1.5 минуты, на #6 ~15 сек.
Теперь дожидаемся опросом GET /api/uploadposts/status?request_id=... до
completed и показываем реальный post_url. Не заявлять об успехе по коду ответа.

ДВА РЕЖИМА:
  карусель — POST /api/upload_photos, файлы в photos[] (до 10)
  reels    — POST /api/upload,        файл в video, media_type=REELS

ПЛОЩАДКИ — --to, ПО УМОЛЧАНИЮ instagram. Одна отправка может уйти сразу на
несколько: --to instagram,telegram. Умолчание оставлено прежним намеренно, чтобы
все старые вызовы работали как раньше.

  ФОТО И ВИДЕО — РАЗНЫЕ СПИСКИ ПЛОЩАДОК. Видео (/api/upload) принимают все,
  YouTube в том числе. А фото (/api/upload_photos) — LinkedIn, Facebook, X,
  Instagram, TikTok, Threads, Pinterest, Reddit, Bluesky, Discord, Telegram,
  Google Business. YOUTUBE В ФОТО-СПИСКЕ НЕТ, и это не пробел сервиса:
  публичного API для Community-постов у YouTube не существует вовсе. Поэтому
  --dir --to youtube роняем сразу, с объяснением, а не ждём ошибки от сервиса.

  PINTEREST ТРЕБУЕТ ДОСКУ: pinterest_board_id обязателен, задаётся
  --pinterest-board. Без него сервис ответит ошибкой — проверяем заранее.

  TELEGRAM КЛАДЁТ КАРУСЕЛЬ АЛЬБОМОМ (медиагруппа), а не свайпом. Технически
  проходит, но обложка перестаёт работать обложкой: видны сразу все превью.
  Значит смысл в ТГ несёт ПОДПИСЬ, а не первый слайд.

ХЕШТЕГИ И АТРИБУЦИЯ — В ПЕРВЫЙ КОММЕНТАРИЙ (--first-comment / файл
first_comment_<имя>.txt). Комментарии двигают пост, а описание не стоит забивать
хвостами. Туда же обязана уходить строка за музыку CC-BY (условие лицензии).

ЗАПУСК:
    python autopost.py --dir out_c6                      # показать, не отправляя
    python autopost.py --dir out_c6 --send               # опубликовать сейчас
    python autopost.py --dir out_c6 --send --at 2026-07-18T10:00:00Z
    python autopost.py --dir out_c6 --send --queue       # в очередь сервиса
    python autopost.py --video out_reels/r1.mp4          # Reels, пробный прогон
    python autopost.py --video out_reels/r1.mp4 --send   # Reels, публикация
    python autopost.py --video out_reels/r4.mp4 --to youtube,telegram --send
    python autopost.py --dir out_c9 --to pinterest --pinterest-board 12345 --send
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")

# ЕДИНЫЙ РИДЕР НАСТРОЕК. Мастер установки кладёт ответы клиента В ДВА ФАЙЛА:
# секретные (`"secret": true` в манифесте) — в `.env`, остальные — в
# `config/factory.config.json`. Этот скрипт до 22.08.2026 читал только `.env`,
# поэтому заведённый клиентом UPLOAD_POST_USER (он помечен «не секрет» —
# это имя профиля, а не пароль) не доходил сюда никогда, и первая же публикация
# падала на пустом месте. Ридер общий с modules/publishing/doctor.py — чтобы
# доктор и боевой код не могли разойтись во мнении «всё ли заполнено».
sys.path.append(os.path.join(HERE, "modules"))
try:
    from publishing import config_reader
except Exception:          # обрезанная копия без modules/ — работаем как раньше
    config_reader = None

API = "https://api.upload-post.com/api/upload_photos"
API_VIDEO = "https://api.upload-post.com/api/upload"
API_STATUS = "https://api.upload-post.com/api/uploadposts/status"
MAX_PHOTOS = 10          # предел карусели в Instagram

# ЖУРНАЛ ПУБЛИКАЦИЙ (шов с аналитикой). При РЕАЛЬНОЙ отправке (--send, получен
# request_id) дописываем сюда связь «наш пост ↔ request_id», чтобы живой сбор
# modules/analytics.providers.fetch_live потом привязал метрики к посту по
# request_id/user (см. DESIGN §7). Путь относительный от корня (HERE), не
# абсолютная константа; каталог гитигнорится (см. _state_dir). В dry-run сюда
# не заходим — реального request_id нет, сохранять нечего.
STATE_DIR = os.path.join(HERE, ".state")
PUBLISHED_LOG = os.path.join(STATE_DIR, "published_ids.jsonl")

# Площадки, которые фото-эндпоинт НЕ принимает вовсе (сверено с доками
# 21.07.2026). У YouTube это не ограничение сервиса, а отсутствие публичного
# API для Community-постов — обойти нечем, только руками. Pinterest СЮДА НЕ
# входит: карусель туда уходит, просто автоматически ужимается до обложки
# (см. main(), «карусель в Pinterest уходит ТОЛЬКО обложкой»).
NO_PHOTOS = {"youtube"}

# ПОТОЛКИ ТЕКСТА ПО ПЛОЩАДКАМ (символы). Считаем ДО отправки: сервис либо молча
# обрежет, либо ответит ошибкой, и узнаем мы об этом из ленты, а не из лога.
# У YouTube и Pinterest ЗАГОЛОВОК — это отдельное короткое поле, а не подпись:
# без него туда уезжает вся подпись целиком и упирается в 100 символов.
LIMITS = {
    "youtube_title": 100,
    "pinterest_title": 100,
    # ЗАМЕРЕНО ЖИВЬЁМ 22.08: Pinterest отдал 400 «description is too long (1525
    # characters). Maximum allowed is 500» на первой же пробной публикации —
    # в доках upload-post.com этого лимита не было, узнали только по отказу.
    "pinterest_description": 500,
    "threads": 500,
    "instagram": 2200,
    # ЗАМЕРЕНО ЖИВЬЁМ 22.07: Telegram отдал 400 «title is too long (1234), maximum
    # 1024» на карусель #8. Это потолок ПОДПИСИ К АЛЬБОМУ у самого Telegram, а не
    # выдумка сервиса. Тексты под архив пишутся сразу короче.
    "telegram": 1024,
    # TikTok — 22.08.2026 подключён, живьём НЕ ГОНЯЛИ. Потолки по докам
    # upload-post.com/platforms/tiktok: видео 2200 симв. (как Instagram), у
    # фото-постов (карусель/слайд-шоу) жёстче — 90. Обрезаем сами перед
    # отправкой (см. tiktok_caption_for), а не полагаемся на сервис — не
    # проверено, обрежет он молча или откажет 400.
    "tiktok_video": 2200,
    "tiktok_photo": 90,
}

# ОДНО СОЕДИНЕНИЕ НА ВЕСЬ ПРОГОН. Замер 06.08.2026: новое TLS-рукопожатие на
# каждый вызов — 2.84 сек, через Session — 0.6 сек. Поллинг статуса делает
# десятки вызовов подряд, и на них это складывается в минуты пустого ожидания.
S = requests.Session()

# ВРЕМЕННЫЕ ОТКАЗЫ ШЛЮЗА, которые осмысленно повторять. 06.08.2026 сервис
# ответил 502 посреди выпуска — скрипт вышел с ошибкой, и человек повторял
# команду руками. 4xx сюда НЕ входят: это наша ошибка (ключ, тариф, длина
# заголовка), повтор её не лечит, а только маскирует.
RETRY_HTTP = {429, 500, 502, 503, 504}
RETRY_PAUSE = (2, 6, 15)

# КОДЫ ВОЗВРАТА. До 22.08.2026 скрипт печатал «НЕ ВЫШЛО: {…}» и всё равно
# уходил с нулём. Выпуск (release.py) судит об исходе ТОЛЬКО по коду возврата —
# и отказанная площадка попадала в итог как «ОК», просто без ссылки. Человек не
# замечал. Теперь провал виден кодом.
#   0 — сервис сказал completed и все площадки ответили успехом;
#   1 — ОТКАЗ: публикации нет (сервис отбил отправку или вернул success:false);
#   3 — НЕ ПОДТВЕРЖДЕНО: пост мог выйти, а мог и нет (ждали дольше отведённого,
#       не смогли спросить статус, ответ без результатов). Отдельный код нужен
#       затем, чтобы выпуск не звал повтор там, где повтор сделает ДУБЛЬ.
# Код 2 занят гейтом активации (activation_gate) — не переиспользуем.
EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_UNCONFIRMED = 3


def post_with_retry(url, key, data, files_factory, timeout, tries=3):
    """POST с повтором на временных отказах.

    `files_factory` — функция, возвращающая СВЕЖИЙ список открытых файлов:
    повторить отправку теми же дескрипторами нельзя, они уже вычитаны до конца.
    Закрываем их сами после каждой попытки.
    """
    last = None
    for attempt in range(tries):
        opened, payload = files_factory()
        try:
            r = S.post(url, headers={"Authorization": f"Apikey {key}"},
                       data=data, files=payload, timeout=timeout)
        except requests.RequestException as e:
            last = e
            if attempt == tries - 1:
                raise
            print(f"   связь оборвалась ({type(e).__name__}) — повтор "
                  f"{attempt + 2}/{tries} через {RETRY_PAUSE[attempt]} сек")
            time.sleep(RETRY_PAUSE[attempt])
            continue
        finally:
            for f in opened:
                f.close()
        if r.status_code in RETRY_HTTP and attempt < tries - 1:
            print(f"   сервис ответил {r.status_code} — повтор "
                  f"{attempt + 2}/{tries} через {RETRY_PAUSE[attempt]} сек")
            time.sleep(RETRY_PAUSE[attempt])
            last = r
            continue
        return r
    return last


def load_env(path=ENV):
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


CREDS_KEYS = ("UPLOAD_POST_API_KEY", "UPLOAD_POST_USER")


def creds():
    """Ключ и профиль Upload-Post — ИЗ ОБОИХ ФАЙЛОВ НАСТРОЕК, а не только из .env.

    Ключ помечен в манифесте секретом и лежит в `.env`; имя профиля секретом не
    помечено и лежит в `config/factory.config.json`. Раньше здесь читался только
    `.env`, и заполненный мастером профиль сюда не доходил вовсе.

    Порядок источников — как у доктора: os.environ > .env > factory.config.json.
    """
    if config_reader is not None:
        got = config_reader.values(CREDS_KEYS, start=HERE)
    else:
        env = load_env()
        got = {k: (os.environ.get(k) or env.get(k) or None) for k in CREDS_KEYS}
    lack = [k for k in CREDS_KEYS if not got.get(k)]
    if lack:
        if config_reader is not None:
            env_file = config_reader.env_path(HERE)
            cfg_file = config_reader.config_path(HERE)
        else:
            env_file = ENV
            cfg_file = os.path.join(HERE, "config", "factory.config.json")
        human = {"UPLOAD_POST_API_KEY": "ключ сервиса публикации",
                 "UPLOAD_POST_USER": "название профиля в сервисе публикации"}
        sys.exit(
            "ПУБЛИКАЦИЯ ОСТАНОВЛЕНА — не заполнено: "
            + ", ".join(human[k] for k in lack) + ".\n"
            "\n"
            "Что это. Ключ — код доступа из личного кабинета upload-post.com.\n"
            "Профиль — короткое имя, под которым вы завели там свои соцсети\n"
            "(это НЕ пароль).\n"
            "\n"
            "Как починить: пройдите мастер установки ещё раз — он спросит оба\n"
            "значения и разложит их по местам сам.\n"
            "\n"
            "Если правите руками, лежат они в РАЗНЫХ файлах:\n"
            f"    UPLOAD_POST_API_KEY (секрет)  -> {env_file}\n"
            f"    UPLOAD_POST_USER              -> {cfg_file}\n"
            "\n"
            "Сам аккаунт и подключение Instagram делаются на upload-post.com "
            "руками."
        )
    return got["UPLOAD_POST_API_KEY"], got["UPLOAD_POST_USER"]


def slides(folder):
    """PNG карусели по порядку: 01.png, 02.png, ... Порядок = порядок листания."""
    d = folder if os.path.isabs(folder) else os.path.join(HERE, folder)
    if not os.path.isdir(d):
        sys.exit(f"НЕТ ПАПКИ: {d}")
    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
    if not files:
        sys.exit(f"В {d} нет PNG.")
    if len(files) > MAX_PHOTOS:
        sys.exit(f"В {d} {len(files)} слайдов, Instagram берёт максимум {MAX_PHOTOS}.")
    return [os.path.join(d, f) for f in files]


def caption_for(folder, explicit=None):
    """Описание поста: из --caption-file, иначе caption_<папка>.txt рядом.

    Для видео <папка> — имя файла без расширения: out_reels/r1_x.mp4 →
    caption_r1_x.txt.
    """
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(HERE, explicit)
    else:
        base = os.path.basename(folder.rstrip("/\\"))
        base = os.path.splitext(base)[0]
        p = os.path.join(HERE, f"caption_{base}.txt")
    if not os.path.exists(p):
        sys.exit(f"НЕТ ОПИСАНИЯ ПОСТА: {p}\n"
                 "Опиши пост в этом файле (2–4 строки + кодовое слово).")
    with open(p, encoding="utf-8") as f:
        return f.read().strip()


def first_comment_for(target, explicit=None):
    """Первый комментарий: хештеги, хвосты и атрибуция за музыку CC-BY.

    Файл необязателен: нет — значит комментария не будет. Но если в ролике
    музыка CC-BY, строка атрибуции обязана уйти либо сюда, либо в описание —
    это условие лицензии, а не вкус.
    """
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(HERE, explicit)
    else:
        base = os.path.splitext(os.path.basename(target.rstrip("/\\")))[0]
        p = os.path.join(HERE, f"first_comment_{base}.txt")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read().strip() or None


def short_title(cap, target, explicit=None):
    """Короткий ЗАГОЛОВОК для YouTube и Pinterest — это не подпись.

    Берём из title_<имя>.txt, если он есть. Если нет — вытаскиваем первую
    непустую строку подписи и режем по границе слова. Возвращаем (текст, сам_ли
    придумал), чтобы вызывающий честно сказал, что заголовок выведен, а не
    написан руками: у роликов первая строка подписи бывает «1. Ты не знаешь,
    что ему поручить» — как заголовок это плохо.
    """
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(HERE, explicit)
    else:
        base = os.path.splitext(os.path.basename(target.rstrip("/\\")))[0]
        p = os.path.join(HERE, f"title_{base}.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t, False
    first = next((s.strip() for s in cap.splitlines() if s.strip()), "")
    cap_len = LIMITS["youtube_title"]
    if len(first) > cap_len:
        cut = first[:cap_len]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        first = cut.rstrip(" ,.;:—-")
    return first, True


def check_lengths(plats, cap, title, threads_cap="", tiktok_cap="", is_video=False):
    """Померить тексты против потолков площадок. Возвращает список претензий."""
    bad = []
    if "youtube" in plats and len(title) > LIMITS["youtube_title"]:
        bad.append(f"заголовок YouTube {len(title)} симв. при потолке "
                   f"{LIMITS['youtube_title']}")
    if "pinterest" in plats and len(title) > LIMITS["pinterest_title"]:
        bad.append(f"заголовок Pinterest {len(title)} симв. при потолке "
                   f"{LIMITS['pinterest_title']}")
    if "pinterest" in plats and len(cap) > LIMITS["pinterest_description"]:
        bad.append(f"описание Pinterest {len(cap)} симв. при потолке "
                   f"{LIMITS['pinterest_description']} — сервис ОТКАЖЕТ (400), "
                   "обрежем сами перед отправкой")
    # Для Threads считаем ЕГО текст, если он задан отдельным файлом: иначе общая
    # длинная подпись подняла бы ложную тревогу на выпуске, где короткая версия
    # уже написана.
    t_cap = threads_cap or cap
    if "threads" in plats and len(t_cap) > LIMITS["threads"]:
        bad.append(f"подпись для Threads {len(t_cap)} симв. при потолке "
                   f"{LIMITS['threads']} — текст обрежется")
    if "instagram" in plats and len(cap) > LIMITS["instagram"]:
        bad.append(f"подпись для Instagram {len(cap)} симв. при потолке "
                   f"{LIMITS['instagram']}")
    # Telegram отказывает по 400, а не обрезает: пост не уходит вовсе.
    if "telegram" in plats and len(cap) > LIMITS["telegram"]:
        bad.append(f"подпись для Telegram {len(cap)} симв. при потолке "
                   f"{LIMITS['telegram']} — сервис ОТКАЖЕТ, пост не уйдёт")
    if "tiktok" in plats:
        tt = tiktok_cap or cap
        tt_limit = LIMITS["tiktok_video"] if is_video else LIMITS["tiktok_photo"]
        if len(tt) > tt_limit:
            kind = "" if is_video else " (у фото-постов TikTok потолок жёстче видео)"
            bad.append(f"подпись для TikTok {len(tt)} симв. при потолке "
                       f"{tt_limit}{kind} — обрежем сами перед отправкой")
    return bad


MIN_SHARE = 0.8  # правило владельца 25.08.2026: не меньше 80% потолка площадки


def strict_captions():
    """Останавливать ли выпуск из-за недожатой подписи, или только предупредить.

    ПОЧЕМУ НЕ ВСЕГДА ЖЁСТКО. Правило максимальной длины — распоряжение владельца
    для СВОЕГО контента (ПРОЕКТ.md, раздел «ПОДПИСИ»). Этот же `autopost.py`
    уезжает клиентам патчем, а у них правило не обкатано: превратить их рабочую
    публикацию в отказ обновлением, которого они не просили, — это сломать
    людям выпуск чужим решением. Договорённость с соседней машиной 25.08:
    гейт выпускается клиентам отдельно и после обкатки.

    КАК ОТЛИЧАЕМ. Рабочая машина владельца — та, где рядом лежат внутренние
    документы завода (`ПРОЕКТ.md`/`AGENTS.md`). В коробку клиента они не входят
    ни сборщиком, ни патчем (`AGENTS.md` прямо в списке внутреннего), поэтому
    признак не подделывается случайно и не требует ещё одной настройки.
    """
    return any(os.path.exists(os.path.join(HERE, n))
               for n in ("ПРОЕКТ.md", "AGENTS.md"))


def check_min_lengths(plats, cap, threads_cap="", tiktok_cap="", is_video=False):
    """Померить тексты против НИЖНЕЙ границы. Возвращает список претензий.

    ЗАЧЕМ. check_lengths выше ловит только перебор — то, на чём откажет сервис.
    Недобор сервис принимает молча, поэтому его ловили глазами и с опозданием:
    25.08.2026 Валерия увидела на живом посте, что рилс с обещанием «весь список
    в описании» ушёл в Telegram подписью 233 символа при потолке 1024. Замер по
    банку в тот же день: все 38 коротких подписей 216-468 символов, 57 полных из
    95 короче 900 при потолке Instagram 2200. Правило «подписи максимальной
    длины» существовало с 22.08, но жило в переписке — и не соблюдалось. Теперь
    живёт здесь.

    ПОЧЕМУ 80%, А НЕ «БОЛЬШЕ НУЛЯ». Распоряжение владельца — «максимальное
    количество символов и близкое к тому количество для каждой площадки»
    (ПРОЕКТ.md, раздел «ПОДПИСИ»). 80% и есть «близко»: остаётся воздух на темы,
    где дожимать нечем, но обрубок в четверть лимита уже не проходит.

    YOUTUBE ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ: его потолок 5000, требовать 4000 бессмысленно,
    а подпись он получает ту же, что Instagram — её норма его и покрывает.
    """
    bad = []

    def measure(text, limit, human):
        need = int(limit * MIN_SHARE)
        if len(text) < need:
            bad.append(u"подпись для %s НЕДОЖАТА: %d симв. при потолке %d, "
                       u"норма от %d (80%%). Не хватает %d — раскрой мысль "
                       u"конкретикой, список отдай целиком, не сокращай"
                       % (human, len(text), limit, need, need - len(text)))

    if "instagram" in plats:
        measure(cap, LIMITS["instagram"], u"Instagram")
    if "telegram" in plats:
        measure(cap, LIMITS["telegram"], u"Telegram")
    if "threads" in plats:
        measure(threads_cap or cap, LIMITS["threads"], u"Threads")
    if "pinterest" in plats:
        measure(cap, LIMITS["pinterest_description"], u"Pinterest")
    if "tiktok" in plats and is_video:
        measure(tiktok_cap or cap, LIMITS["tiktok_video"], u"TikTok")
    return bad


def wait_done(key, request_id, timeout=600, step=5):
    """Дождаться реального результата. 200 на загрузку — это ещё не публикация.

    Сервис отдаёт request_id и передаёт работу воркеру. Замеры: #5 обрабатывалась
    ~1.5 минуты, #6 ~15 секунд. Поэтому опрашиваем до completed/failed, а не
    объявляем успех по коду ответа.

    ШАГ ОПРОСА АДАПТИВНЫЙ. Telegram завершается за 2 секунды и не должен ждать
    пятисекундного тика; долгие ноги (Threads — 85 сек замером) незачем дёргать
    часто. Сбой САМОГО ОПРОСА больше не считается провалом публикации: пост в
    этот момент уже у сервиса, и 502 на статусе означает лишь «спроси ещё раз».
    """
    t0 = time.time()
    last = None
    errors = 0
    while time.time() - t0 < timeout:
        waited = time.time() - t0
        step = 1 if waited < 15 else (3 if waited < 60 else 5)
        try:
            r = S.get(API_STATUS, params={"request_id": request_id},
                      headers={"Authorization": f"Apikey {key}"}, timeout=60)
        except requests.RequestException as e:
            errors += 1
            if errors > 5:
                return {"status": "status_error", "http": 0, "body": str(e)[:400]}
            time.sleep(step)
            continue
        if r.status_code >= 400:
            errors += 1
            if errors > 5:
                return {"status": "status_error", "http": r.status_code,
                        "body": r.text[:400]}
            time.sleep(step)
            continue
        try:
            d = r.json()
        except ValueError:
            errors += 1
            if errors > 5:
                return {"status": "status_error", "http": r.status_code,
                        "body": r.text[:400]}
            time.sleep(step)
            continue
        st = d.get("status")
        if st != last:
            print(f"   статус: {st}  ({int(time.time() - t0)} сек)")
            last = st
        if st in ("completed", "failed", "error"):
            return d
        time.sleep(step)
    return {"status": "timeout", "waited": timeout}


def went_to_inbox(res):
    """Правда ли, что этот «успех» на самом деле лёг в черновики приложения.

    Сервис при упоре в суточный лимит TikTok отвечает success:true с настоящим
    id и ссылкой, а единственный признак подмены — fallback_to_inbox:true.

    Ищем ОБХОДОМ, а не по фиксированному ключу: в ответах 28.08.2026 флаг лежал
    и в корне результата площадки, и внутри вложенного блока с ответом TikTok, —
    форма ответа у сервиса за лето менялась дважды, и прибитый путь
    res["fallback_to_inbox"] промолчит ровно в том случае, ради которого всё это
    и написано. Молчание тут стоит клиенту ноля просмотров."""
    if isinstance(res, dict):
        for k, v in res.items():
            # JSON-true, но сервис в части ответов кладёт строку — считаем оба.
            if k == "fallback_to_inbox" and (
                    v is True or (isinstance(v, str) and v.lower() == "true")):
                return True
            if went_to_inbox(v):
                return True
        return False
    if isinstance(res, (list, tuple)):
        return any(went_to_inbox(x) for x in res)
    return False


def report(d):
    """Показать, что реально произошло, без приукрашивания."""
    st = d.get("status")
    if st == "timeout":
        print(f"\n! Сервис не ответил completed за {d['waited']} сек. Это НЕ значит "
              f"«не опубликовано» — проверь ленту и статус по request_id.")
        return
    if st == "status_error":
        print(f"\n! Опрос статуса дал {d['http']}: {d['body']}")
        return
    results = d.get("results") or []
    ok = [r for r in results if r.get("success") and not went_to_inbox(r)]
    inbox = [r for r in results if r.get("success") and went_to_inbox(r)]
    bad = [r for r in results if not r.get("success")]
    print(f"\nСтатус: {st}")
    for r in ok:
        print("   ОПУБЛИКОВАНО:", r.get("post_url") or "(ссылки нет в ответе)")
    for r in inbox:
        # Это НЕ публикация, как бы ни выглядел ответ сервиса.
        print("   НЕ ОПУБЛИКОВАНО, ЛЕГЛО В ЧЕРНОВИКИ TikTok:",
              r.get("post_url") or "(ссылки нет в ответе)")
        print("      Ролик ждёт в приложении на телефоне. Пока его не "
              "допубликуют РУКАМИ (TikTok → уведомления/черновики), его не "
              "увидит никто: ноль просмотров, посторонний ссылку не откроет.")
    for r in bad:
        print("   НЕ ВЫШЛО:", json.dumps(r, ensure_ascii=False)[:400])
    if not results:
        print("   ответ без results:", json.dumps(d, ensure_ascii=False)[:600])


def verdict(d):
    """Чем ДЕЙСТВИТЕЛЬНО кончилась публикация → (код возврата, объяснение).

    Отделено от report() нарочно: report печатает, verdict решает. Решение нужно
    отдельной функцией, потому что его читает не только человек, но и release.py
    с publish_queue.py — по коду возврата процесса.

    Успех — это completed И все площадки с success:true. Всё прочее провал или
    неизвестность, и мы говорим об этом прямо, а не молчим с нулём.
    """
    st = str(d.get("status") or "").lower()
    results = d.get("results") or []

    if st == "timeout":
        return EXIT_UNCONFIRMED, (
            "сервис не сказал «готово» за %s сек. Это НЕ значит «не "
            "опубликовано»: пост мог выйти. Посмотрите ленту глазами, повтор "
            "вслепую сделает дубль." % d.get("waited", "?"))
    if st == "status_error":
        return EXIT_UNCONFIRMED, (
            "не удалось спросить сервис о результате (сам пост он к тому "
            "моменту уже принял). Посмотрите ленту глазами, повтор вслепую "
            "сделает дубль.")
    if st in ("failed", "error"):
        return EXIT_REJECTED, "сервис сообщил об ошибке публикации — поста нет."

    bad = [r for r in results if not r.get("success")]
    if bad:
        return EXIT_REJECTED, (
            "площадок с отказом: %d из %d — там поста нет." % (len(bad), len(results)))
    if st != "completed":
        return EXIT_UNCONFIRMED, (
            "сервис вернул незнакомое состояние «%s» вместо «готово»."
            % (d.get("status") or "—"))
    if not results:
        return EXIT_UNCONFIRMED, (
            "сервис сказал «готово», но не назвал ни одной площадки — "
            "подтвердить публикацию нечем.")
    # СТРОГО ДО финального EXIT_OK: у таких результатов success:true и ссылка,
    # и по всем проверкам выше они выглядят удачей. Удачей они не являются.
    inbox = [r for r in results if went_to_inbox(r)]
    if inbox:
        # Площадки называем поимённо. Выкладка почти всегда идёт сразу на
        # несколько, и «пост не вышел» без имени — неправда: в Instagram он в
        # этот же заход мог выйти штатно. Человек по такому тексту полезет
        # перевыкладывать ВСЁ и сделает дубли там, где всё было хорошо.
        names = ", ".join(sorted(
            str(r.get("platform") or r.get("platform_name") or "?")
            for r in inbox)) or "?"
        return EXIT_UNCONFIRMED, (
            "%s: пост НЕ вышел — площадка упёрлась в суточный лимит, и сервис "
            "положил ролик в ЧЕРНОВИКИ приложения. Его надо допубликовать "
            "руками на телефоне, иначе его не увидит никто. Повтор вслепую "
            "сделает второй черновик, а не пост. Остальные площадки этого "
            "захода смотрите выше — там всё могло выйти штатно." % names)
    return EXIT_OK, ""


def service_hint(status, body):
    """Русская подсказка по отказу сервиса: что случилось и что сделать.

    Сервис отвечает по-английски и телеграфно («Profile not found»), а читает
    это человек, который слова «API» не знает. Раньше подсказки были только на
    401 и 402, а САМЫЙ ЧАСТЫЙ отказ у нового клиента — 400 «профиль не найден»:
    ключ заведён, а профиля на upload-post.com либо нет, либо он назван иначе,
    чем вписано в мастере. Клиент видел английскую строчку и шёл спрашивать.

    Разбираем по СЛОВАМ ответа, а не только по коду: сервис кладёт причину в
    текст, а код у половины случаев один и тот же — 400. Подсказку держим
    короткой (до шести строк): при выпуске через release.py в отчёт попадает
    последний хвост лога, и длинная подсказка из него вывалится.

    Пустая строка — «своими словами сказать нечего», тогда виден дословный ответ.
    """
    t = (body or "").lower()

    # ── ключ не подошёл ─────────────────────────────────────────────────────
    if (status in (401, 403) or "invalid api key" in t or "unauthorized" in t
            or "invalid token" in t or "forbidden" in t):
        return ("ЧТО СЛУЧИЛОСЬ: сервис публикации не признал ваш ключ доступа.\n"
                "ЧТО СДЕЛАТЬ: откройте личный кабинет на upload-post.com, "
                "скопируйте ключ заново\n"
                "и впишите его в мастере установки — вопрос «Ключ сервиса "
                "публикации Upload-Post».\n"
                "Копируйте целиком, без пробелов по краям. Ключ действует, пока "
                "оплачен тариф.")

    # ── профиль не найден ───────────────────────────────────────────────────
    # Это ключевой случай первой недели: ключ верный, а имени профиля,
    # вписанного в мастере, на upload-post.com не существует.
    if (("profile" in t or "user" in t or "account" in t)
            and ("not found" in t or "does not exist" in t or "no such" in t
                 or "unknown" in t or "invalid" in t)):
        return ("ЧТО СЛУЧИЛОСЬ: на upload-post.com нет профиля с тем названием, "
                "которое вы вписали.\n"
                "Профиль — это карточка, к которой вы подключаете свои соцсети; "
                "он создаётся отдельно\n"
                "от ключа, и часто про него просто забывают.\n"
                "ЧТО СДЕЛАТЬ: зайдите на upload-post.com, откройте список "
                "профилей. Если профиля нет —\n"
                "создайте. Если есть — перепишите его название БУКВА В БУКВУ в "
                "мастере установки\n"
                "(вопрос «Название вашего профиля в Upload-Post»): большие и "
                "маленькие буквы важны.")

    # ── соцсеть не подключена к профилю ─────────────────────────────────────
    if ("not connected" in t or "no connected" in t or "not linked" in t
            or "no account" in t or "missing account" in t
            or "no platform" in t or "not authenticated" in t
            or "reconnect" in t or "expired" in t):
        return ("ЧТО СЛУЧИЛОСЬ: профиль на upload-post.com есть, но нужная "
                "соцсеть к нему не подключена\n"
                "(или подключение отвалилось — соцсети сбрасывают доступ сами, "
                "обычно раз в пару месяцев).\n"
                "ЧТО СДЕЛАТЬ: зайдите на upload-post.com, откройте свой профиль "
                "и нажмите «подключить»\n"
                "у той площадки, куда шёл пост. Instagram при этом должен быть "
                "переведён\n"
                "в «профессиональный» аккаунт и связан со страницей Facebook — "
                "иначе он не подключается.")

    # ── оплата и квоты ──────────────────────────────────────────────────────
    # «limit» сюда НЕ берём: сервис пишет так и про длину заголовка
    # («title is too long»), а это совсем другая беда и другая починка.
    if (status == 402 or "payment" in t or "subscription" in t
            or "quota" in t or "upgrade" in t or "credits" in t):
        return ("ЧТО СЛУЧИЛОСЬ: сервис отказал по оплате или по квоте.\n"
                "С 21.07 стоит тариф «Базовый» ($24/мес) с БЕЗЛИМИТНЫМИ "
                "публикациями — значит дело\n"
                "не в счётчике постов.\n"
                "ЧТО СДЕЛАТЬ: проверьте в кабинете upload-post.com, прошло ли "
                "списание за месяц,\n"
                "и не израсходована ли квота обработки видео (300 минут в месяц).")

    if status == 400:
        return ("ЧТО СЛУЧИЛОСЬ: сервис не принял отправку и назвал причину "
                "по-английски — она в строке выше.\n"
                "ЧТО СДЕЛАТЬ: чаще всего это профиль (название вписано не так, "
                "как на upload-post.com),\n"
                "неподключённая соцсеть или слишком длинная подпись. "
                "Проверьте по очереди эти три.\n"
                "Если не помогло — покажите строку выше человеку, который "
                "настраивал завод.")
    return ""


def _fail(r):
    """Отказ сервиса на самой отправке. Выходим с кодом 1 — публикации НЕТ.

    Порядок печати не косметика: русская подсказка идёт ПОСЛЕДНЕЙ, потому что
    release.py показывает в отчёте последние шесть строк лога упавшей ноги.
    """
    print(f"\nСЕРВИС ПУБЛИКАЦИИ ОТКАЗАЛ (код {r.status_code}). ПОСТ НЕ ВЫШЕЛ.")
    print("Дословный ответ сервиса:", (r.text or "")[:800])
    hint = service_hint(r.status_code, r.text)
    if hint:
        print()
        print(hint)
    sys.exit(EXIT_REJECTED)


def _state_dir():
    """Каталог рантайм-журнала публикаций. Создаётся ЛЕНИВО, при первой реальной
    отправке, и гитигнорится: кладём внутрь `.gitignore` с `*` — ровно как модуль
    analytics прячет свои рантайм-данные (ensure_dirs). Это не секрет и не код, а
    рантайм-состояние конкретного аккаунта — в репозиторий ему нельзя. Каталог и
    его создание кроссплатформенны (os.makedirs, целевая ОС — Windows)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    gi = os.path.join(STATE_DIR, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("*\n")
    return STATE_DIR


def _already_logged(path, request_id, platform):
    """Уже есть строка с этим request_id для этой площадки? Тогда повторный прогон
    не задваивает журнал — идемпотентность к повторам. Битые строки пропускаем,
    журнал одного сбоя разбора не роняет."""
    if not request_id or not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("request_id") == request_id
                    and rec.get("platform") == platform):
                return True
    return False


def persist_published(request_id, plats, user, material, media_type, status,
                      scheduled_for=None, trial=None, results=None):
    """Сохранить связь «наш пост ↔ request_id» в гит-игнорируемый журнал JSONL.

    ПО СТРОКЕ НА ПЛОЩАДКУ. analytics.providers.fetch_live читает у карточки ОДНУ
    `platform` (по ней выбирает провайдера) плюс `request_id`, `user`, `post_id`
    — поля называем ровно так, как их читает fetch_live, чтобы аналитика взяла
    журнал без переходника. `post_id` делаем уникальным на площадку
    (`<материал>_<площадка>`): хранилище метрик перезаписывает записи по post_id.
    Дополнительно кладём `platforms` (весь список), `material`, `media_type`,
    `published_at` (ISO-8601, UTC) и `status`.

    `results` (ответ сервиса по площадкам, если он к этому моменту есть) кладём
    в запись двумя полями: `post_url` и `fallback_to_inbox`. Причина замерена
    28.08.2026: у TikTok обнаружилась тихая подмена публикации на черновик, и
    выяснить, срабатывала ли она на ПРОШЛЫХ публикациях, оказалось негде —
    ответ площадки нигде не сохранялся. Поля только ДОБАВЛЯЮТСЯ: analytics.
    providers.fetch_live привязывается по `request_id` и старую форму записи
    читает как раньше.

    Зовётся ТОЛЬКО при реальной отправке (есть request_id). Append одним write;
    строка с тем же (request_id, площадка) не задваивается. Возвращает путь к
    журналу или None, если писать было нечего."""
    if not request_id:
        return None
    _state_dir()
    now = datetime.now(timezone.utc).isoformat()
    lines = []
    for p in plats:
        if _already_logged(PUBLISHED_LOG, request_id, p):
            continue
        rec = {
            "request_id": request_id,
            "platform": p,
            "platforms": plats,
            "user": user,
            "post_id": f"{material}_{p}",
            "material": material,
            "media_type": media_type,
            "published_at": now,
            "status": status,
        }
        # НА КАКУЮ ДАТУ ОТЛОЖЕН пост. Без этого поля журнал знает только «когда
        # мы отправили заявку», а это у отложенных публикаций совсем другой
        # момент — иногда на две недели раньше выхода. Суточный отчёт
        # (daily_report.py) считает по нему и «что ещё ждёт выхода», и «до какой
        # даты вообще расписан контент», и расхождения «обещали на дату — не
        # вышло».
        if scheduled_for:
            rec["scheduled_for"] = scheduled_for
        # Пробный показ Instagram Trial Reels — в отчёте считается отдельной
        # строкой: это принципиально другой охват (сначала не-подписчики).
        # ЧТО ОТВЕТИЛА ПЛОЩАДКА. Ссылка на пост и признак «легло в черновики» —
        # только по ним потом можно задним числом сказать, был ли пост виден
        # людям вообще. Ответ сервиса по площадкам бывает и списком, и словарём
        # с ключом-площадкой, поэтому достаём и так, и так.
        res = None
        if isinstance(results, dict):
            res = results.get(p)
        elif isinstance(results, (list, tuple)):
            for r in results:
                if isinstance(r, dict) and str(
                        r.get("platform") or r.get("platform_name") or ""
                ).lower() == p:
                    res = r
                    break
            # Площадка одна и результат один — сопоставлять больше не с чем.
            if res is None and len(plats) == 1 and len(results) == 1 \
                    and isinstance(results[0], dict):
                res = results[0]
        if isinstance(res, dict):
            rec["post_url"] = res.get("post_url")
            rec["fallback_to_inbox"] = went_to_inbox(res)
        if trial:
            rec["trial"] = trial
        lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
    if lines:
        # Один write на все строки — на POSIX и Windows append позиционируется в
        # конец, короткая запись не рвётся посередине конкурентным прогоном.
        with open(PUBLISHED_LOG, "a", encoding="utf-8") as f:
            f.write("".join(lines))
    return PUBLISHED_LOG


def main():
    ap = argparse.ArgumentParser(
        description="Публикация карусели или Reels через Upload-Post")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dir", help="папка со слайдами карусели, напр. out_c6")
    g.add_argument("--video", help="файл ролика для Reels, напр. out_reels/r1.mp4")
    ap.add_argument("--caption-file", help="файл с описанием поста")
    ap.add_argument("--threads-caption-file",
                    help="короткая версия подписи для Threads (потолок 500). "
                         "Позволяет отправить instagram и threads ОДНОЙ заливкой "
                         "вместо двух: слайды уходят один раз, тексты разные")
    ap.add_argument("--first-comment", help="файл с первым комментарием "
                                            "(хештеги, атрибуция за музыку)")
    ap.add_argument("--send", action="store_true",
                    help="ДЕЙСТВИТЕЛЬНО отправить (без него — только показать)")
    ap.add_argument("--short-ok", action="store_true",
                    help=u"разрешить подпись короче нормы (80%% потолка площадки). Только по явному слову: анонс-однодневка, сторис-текст")
    ap.add_argument("--at", help="время публикации, ISO-8601 (2026-07-18T10:00:00Z)")
    ap.add_argument("--queue", action="store_true",
                    help="поставить в очередь сервиса, а не на конкретное время")
    ap.add_argument("--no-feed", action="store_true",
                    help="Reels: не дублировать в ленту (по умолчанию дублируется)")
    ap.add_argument("--trial", choices=["auto", "manual"],
                    help="Reels в Instagram: ПРОБНЫЙ показ — сначала только "
                         "не-подписчикам. auto: сам разойдётся подписчикам, "
                         "если наберёт просмотры за 72 ч (share_mode="
                         "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED). manual: "
                         "остаётся у не-подписчиков, пока не решишь сама в "
                         "приложении (share_mode=TRIAL_REELS_DONT_SHARE_TO_"
                         "FOLLOWERS). Нужен публичный аккаунт от 1000 "
                         "подписчиков — иначе Instagram проигнорирует флаг "
                         "и опубликует как обычный Reels.")
    ap.add_argument("--to", default="instagram",
                    help="площадки через запятую (instagram,youtube,telegram,"
                         "pinterest,tiktok). По умолчанию instagram")
    ap.add_argument("--pinterest-board",
                    help="ID доски Pinterest — обязателен, если в --to есть pinterest")
    ap.add_argument("--pinterest-link",
                    help="куда ведёт пин (pinterest_link) — без него Pinterest "
                         "отказал 'This site doesn't allow you to save Pins' "
                         "(замерено 22.08.2026); по умолчанию берётся "
                         "PINTEREST_LINK из config/factory.config.json")
    ap.add_argument("--tiktok-caption-file",
                    help="короткая подпись для TikTok-карусели (потолок 90 симв.); "
                         "без файла обрежем общую подпись сами")
    ap.add_argument("--tiktok-privacy",
                    choices=["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                             "FOLLOWER_OF_CREATOR", "SELF_ONLY"],
                    default="PUBLIC_TO_EVERYONE",
                    help="приватность поста в TikTok (по докам сервиса без него "
                         "публикация может отказать invalid_params)")
    ap.add_argument("--title-file",
                    help="короткий заголовок для YouTube/Pinterest "
                         "(по умолчанию title_<имя>.txt, иначе выводится сам)")
    a = ap.parse_args()

    target = a.dir or a.video
    is_video = bool(a.video)
    # Материал = имя папки/файла без расширения (как в caption_for/short_title).
    # Идёт в журнал публикаций для связи с аналитикой при реальной отправке.
    material = os.path.splitext(os.path.basename(target.rstrip("/\\")))[0]
    media_type = "reels" if is_video else "carousel"

    plats = [p.strip().lower() for p in a.to.split(",") if p.strip()]
    if not plats:
        sys.exit("--to пустой. Укажи хотя бы одну площадку, напр. --to instagram")

    # Ловим несовместимости ДО отправки: иначе загрузка уйдёт, упадёт на стороне
    # сервиса и молча съест лимит тарифа.
    if not is_video:
        bad = [p for p in plats if p in NO_PHOTOS]
        if bad:
            sys.exit(
                f"КАРУСЕЛЬ НЕЛЬЗЯ ОТПРАВИТЬ НА: {', '.join(bad)}.\n"
                "YouTube — публичного API для Community-постов не существует, "
                "карусель туда можно положить только руками.\n"
                "Видео (--video) на эти площадки уходит нормально."
            )
        # Pinterest: карусель (несколько photos[]) технически уходит, но их
        # приложение НЕ показывает заголовок/описание у карусельных пинов —
        # замерено 22.08.2026 двумя живыми постами (текст есть в логе сервиса,
        # на экране пина его нет вовсе). У ОДИНОЧНОГО фото/видео заголовок и
        # описание видны и, вероятно, только они индексируются поиском — слайды
        # с зашитым текстом поиском не читаются. Поэтому карусель в Pinterest
        # уходит ТОЛЬКО обложкой (1-й слайд) с полным описанием под ней, а не
        # полным набором слайдов без единого слова подписи.
        if "pinterest" in plats and len(plats) > 1:
            sys.exit(
                "PINTEREST В ОДНОМ ВЫЗОВЕ С ДРУГИМИ ПЛОЩАДКАМИ (карусель) "
                "НЕЛЬЗЯ.\nИх карусель не показывает подпись (замерено "
                "22.08) — туда уходит только обложка отдельным вызовом.\n"
                "Позови --to pinterest отдельной командой."
            )
    if "pinterest" in plats and not a.pinterest_board:
        # Доска не обязана приходить флагом каждый раз: подставляем сохранённую
        # (см. PINTEREST_BOARD_ID в config/factory.config.json — узнана через
        # GET /uploadposts/pinterest/boards 22.08.2026).
        a.pinterest_board = config_reader.value("PINTEREST_BOARD_ID") \
            if config_reader else None
        if not a.pinterest_board:
            sys.exit(
                "PINTEREST ТРЕБУЕТ ДОСКУ: добавь --pinterest-board <id>.\n"
                "ID доски берётся из API Pinterest, руками искать не надо."
            )
    if "pinterest" in plats and not a.pinterest_link:
        a.pinterest_link = config_reader.value("PINTEREST_LINK") \
            if config_reader else None

    if is_video:
        p = a.video if os.path.isabs(a.video) else os.path.join(HERE, a.video)
        if not os.path.exists(p):
            sys.exit(f"НЕТ ФАЙЛА: {p}")
        files = [p]
    else:
        files = slides(a.dir)
        if plats == ["pinterest"] and len(files) > 1:
            print(f"Pinterest: карусель не несёт подпись у них в приложении — "
                  f"уходит только обложка (1 из {len(files)} слайдов), "
                  "полный текст остаётся в описании.")
            files = files[:1]

    cap = caption_for(target, a.caption_file)
    fc = first_comment_for(target, a.first_comment)
    # Короткая версия для Threads — необязательна: без неё туда уходит общая
    # подпись, как и раньше.
    threads_cap = caption_for(target, a.threads_caption_file) \
        if a.threads_caption_file else ""
    # TikTok-фото ограничены 90 символами (см. LIMITS) — отдельный файл
    # позволяет написать подпись руками, а не резать общую автоматом.
    tiktok_cap = caption_for(target, a.tiktok_caption_file) \
        if a.tiktok_caption_file else ""

    # ЗАМЕРЕНО 22.07: у Telegram комментариев под постом нет как класса, и сервис
    # кладёт first_comment ОТДЕЛЬНЫМ сообщением в канал. В архиве это мусор, а
    # репостер может утащить его в ВК самостоятельной записью. Гасим сами.
    if fc and plats == ["telegram"]:
        print("Первый комментарий СНЯТ: в Telegram он уходит отдельным постом.")
        fc = ""

    print(f"Что     : {'REELS ' + os.path.basename(files[0]) if is_video else 'карусель ' + a.dir}")
    if is_video:
        # «в ленту» — параметр Instagram (share_to_feed), у других площадок его
        # нет: не показываем то, что никуда не уйдёт.
        feed = ""
        if "instagram" in plats:
            feed = " | в ленту: нет" if a.no_feed else " | в ленту: да"
        print(f"Размер  : {os.path.getsize(files[0]) / 1e6:.1f} МБ{feed}")
    else:
        print(f"Слайдов : {len(files)} -> {', '.join(os.path.basename(f) for f in files)}")
    print(f"Куда    : {', '.join(plats)}")
    if not is_video and "telegram" in plats:
        print("          (в Telegram карусель уйдёт АЛЬБОМОМ — все слайды видны "
              "сразу, обложка не работает крючком)")
    print(f"Когда   : {a.at or ('очередь сервиса' if a.queue else 'сразу')}")
    print("Описание:")
    for line in cap.splitlines():
        print("   ", line)
    if fc:
        print("Первый комментарий:")
        for line in fc.splitlines():
            print("   ", line)
    else:
        print("Первый комментарий: нет")

    needs_title = bool({"youtube", "pinterest"} & set(plats))
    title, derived = short_title(cap, target, a.title_file)
    if needs_title:
        print(f"Заголовок ({len(title)} симв.): {title}")
        if derived:
            base = os.path.splitext(os.path.basename(target.rstrip("/\\")))[0]
            print("   ! Заголовок ВЫВЕДЕН из первой строки подписи, а не написан.")
            print(f"     У YouTube и Pinterest это ЗАГОЛОВОК, его видно в поиске.")
            print(f"     Хочешь свой — положи рядом title_{base}.txt")

    problems = check_lengths(plats, cap, title, threads_cap, tiktok_cap, is_video)
    if problems:
        print("\nНЕ ВЛЕЗАЕТ:")
        for p in problems:
            print("   -", p)

    thin = check_min_lengths(plats, cap, threads_cap, tiktok_cap, is_video)
    if thin:
        print(u"\nНЕДОЖАТО (правило владельца 25.08.2026 — ПРОЕКТ.md, раздел «ПОДПИСИ»):")
        for p in thin:
            print("   -", p)

    if not a.send:
        print("\n[ПРОБНЫЙ ПРОГОН] Ничего не отправлено. Для публикации добавь --send")
        return

    # Гейт активации (BOX-29): реальная публикация только на активированной копии.
    # Пробный прогон (без --send) выше — не гейтим (наружу ничего не уходит).
    import activation_gate
    if not activation_gate.allow("публикацию"):
        sys.exit(2)

    # Заголовок сверх потолка — это отказ площадки, а не косметика: роняем сами,
    # чтобы не гадать по чужой ошибке. Длинная подпись всего лишь обрежется,
    # поэтому она остаётся предупреждением.
    hard = [p for p in problems if "заголовок" in p]
    if hard:
        sys.exit("ОТПРАВКА ОСТАНОВЛЕНА — " + "; ".join(hard) +
                 ".\nСократи заголовок в title_<имя>.txt.")

    # НЕДОЖАТАЯ ПОДПИСЬ ТОЖЕ ОСТАНАВЛИВАЕТ ОТПРАВКУ. Площадка её примет,
    # поэтому без гейта дефект уходит в ленту и ловится глазами через сутки —
    # так было 23.08 (r118) и 25.08 (r67). Клапан --short-ok для осознанных
    # случаев, требует явного слова.
    if thin and not strict_captions():
        print("\n   ⚠️ Подпись короче нормы. Это не отказ: допишите, если хотите "
              "выжать площадку полностью.")
    elif thin and not getattr(a, "short_ok", False):
        sys.exit(u"ОТПРАВКА ОСТАНОВЛЕНА — подпись недожата:\n  - "
                 + u"\n  - ".join(thin)
                 + u"\nДопиши подпись до нормы. Если коротко ОСОЗНАННО — "
                   u"повтори команду с --short-ok.")
    if thin:
        print(u"\n[--short-ok] Отправляю недожатую подпись по явному распоряжению.")

    key, user = creds()
    # requests повторит поле platform[] для каждого элемента списка — сервис ждёт
    # именно так, а не строку через запятую.
    data = {"user": user, "platform[]": plats, "title": cap}
    if a.at:
        data["scheduled_date"] = a.at
    if a.queue:
        data["add_to_queue"] = "true"
    if fc:
        data["first_comment"] = fc
    if "pinterest" in plats:
        data["pinterest_board_id"] = a.pinterest_board
        data["pinterest_title"] = title
        # Pinterest ищет ПО ТЕКСТУ описания: текст на наших слайдах вжжён в
        # картинку и поиском не читается. Без описания пин найти нечем.
        # ЗАМЕРЕНО ЖИВЬЁМ 22.08: потолок 500 симв., сервис на превышении не
        # обрезает, а отказывает целиком (400) — обрезаем сами по границе
        # слова, а не полагаемся на сервис.
        pd = cap
        if len(pd) > LIMITS["pinterest_description"]:
            cut = pd[:LIMITS["pinterest_description"]]
            if " " in cut:
                cut = cut[:cut.rfind(" ")]
            pd = cut.rstrip(" ,.;:—-")
            print(f"Pinterest: описание обрезано до {len(pd)} симв. "
                  f"(потолок {LIMITS['pinterest_description']})")
        data["pinterest_description"] = pd
        if a.pinterest_link:
            # ОБЯЗАТЕЛЕН по факту (замерено 22.08): без ссылки Pinterest отдал
            # "This site doesn't allow you to save Pins" — сайт-заглушка без
            # ссылки в их логике выглядит как чужой сайт, блокирующий пины.
            data["pinterest_link"] = a.pinterest_link
    if "youtube" in plats:
        # Без этих двух полей на YouTube уезжает вся подпись как ЗАГОЛОВОК
        # ролика и упирается в потолок 100 символов.
        data["youtube_title"] = title
        data["youtube_description"] = cap
    if "threads" in plats:
        # У Threads свой потолок 500 символов, поэтому раньше карусель уходила
        # ВТОРОЙ заливкой тех же 8 PNG с коротким текстом. Отдельный файл через
        # --threads-caption-file кладёт короткую версию в собственное поле
        # сервиса — те же слайды заливаются ОДИН раз на обе площадки.
        data["threads_title"] = threads_cap or cap
    if "tiktok" in plats:
        # ОБЯЗАТЕЛЕН по докам TikTok Content Posting API — без него сервис
        # исторически отказывает invalid_params. upload-post.com называет поле
        # опциональным, но это расходится с самим TikTok, поэтому шлём всегда,
        # а не только когда попросили.
        data["privacy_level"] = a.tiktok_privacy
        # ЗАПРЕЩАЕМ ТИХУЮ ПОДМЕНУ (замерено 28.08.2026). С июня 2026 при упоре в
        # суточный лимит TikTok сервис молча переводит публикацию из «выложить»
        # (DIRECT_POST) в «положить в черновики приложения» (MEDIA_UPLOAD) и всё
        # равно отвечает success:true с настоящим id и ссылкой. Ролик ждёт в
        # телефоне, набирает ровно ноль просмотров, посторонний его не откроет, и
        # удалить такой черновик через API нельзя. Пусть лучше сервис ответит
        # честной ошибкой reached_active_user_cap. Булевы флаги здесь идут
        # строками — как share_to_feed ниже.
        data["disable_inbox_fallback"] = "true"
        tt_limit = LIMITS["tiktok_video"] if is_video else LIMITS["tiktok_photo"]
        tt = tiktok_cap or cap
        if len(tt) > tt_limit:
            cut = tt[:tt_limit]
            if " " in cut:
                cut = cut[:cut.rfind(" ")]
            tt = cut.rstrip(" ,.;:—-")
            print(f"TikTok: подпись обрезана до {len(tt)} симв. (потолок {tt_limit})")
        data["tiktok_title"] = tt
        if plats == ["tiktok"]:
            # Единственная площадка вызова — общее «title» это и есть подпись
            # TikTok, шлём ту же обрезанную версию, а не полную.
            data["title"] = tt

    if is_video:
        # Для видео глобальное description сервисом ИГНОРИРУЕТСЯ — подпись идёт
        # через title/instagram_title. Это их задокументированное поведение.
        if "instagram" in plats:
            data["media_type"] = "REELS"
            data["instagram_title"] = cap
            data["share_to_feed"] = "false" if a.no_feed else "true"
            if a.trial:
                data["share_mode"] = (
                    "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED" if a.trial == "auto"
                    else "TRIAL_REELS_DONT_SHARE_TO_FOLLOWERS")
        def _video_files():
            fh = open(files[0], "rb")
            return [fh], [("video", (os.path.basename(files[0]), fh, "video/mp4"))]

        r = post_with_retry(API_VIDEO, key, data, _video_files, 900)
    else:
        def _photo_files():
            opened = [open(f, "rb") for f in files]
            return opened, [("photos[]", (os.path.basename(f.name), f, "image/png"))
                            for f in opened]

        r = post_with_retry(API, key, data, _photo_files, 300)

    if not hasattr(r, "status_code"):
        sys.exit(f"СЕРВИС НЕ ОТВЕТИЛ после повторов: {r}")
    if r.status_code >= 400:
        _fail(r)

    # 200 — это ещё НЕ публикация. Забираем реальный результат опросом.
    try:
        body = r.json()
    except ValueError:
        body = {}
    rid = body.get("request_id") or body.get("requestId")
    print(f"\nПринято сервисом ({r.status_code}). request_id: {rid or '—'}")
    if not rid:
        # Сервис принял отправку, но не назвал заявку — спросить его о судьбе
        # поста больше нечем. Это НЕ успех: уходим кодом «не подтверждено».
        print("Ответ без request_id — показываю как есть, статус проверь руками:")
        print(r.text[:1000])
        print("\nИСХОД НЕ ПОДТВЕРЖДЁН: сервис не выдал номер заявки, проверить "
              "публикацию нечем.\nПосмотрите ленту глазами.")
        sys.exit(EXIT_UNCONFIRMED)
    if a.at or a.queue:
        # Отложенная публикация: результата ещё нет, но request_id уже есть —
        # именно он и нужен аналитике, сохраняем связь сразу.
        #
        # ЗДЕСЬ КОД ВОЗВРАТА ОСТАЁТСЯ НУЛЁМ, и это осознанно: мы отвечаем за
        # ПРИЁМ заявки, а он состоялся. Но честность тут половинчатая —
        # выйдет пост или нет, никто потом не проверяет, и «0» читается как
        # «всё хорошо». Отдельного шага «проверь отложенные» в заводе нет
        # (см. отчёт по задаче 2), поэтому говорим об этом словами.
        persist_published(rid, plats, user, material, media_type,
                          "queued" if a.queue else "scheduled",
                          scheduled_for=a.at, trial=a.trial)
        print("Публикация ОТЛОЖЕНА. Сервис принял заявку — это ещё не пост.")
        print("Выйдет он или нет, автоматически НИКТО не проверит: посмотрите "
              "ленту после назначенного времени.")
        print(f"Связь request_id сохранена в "
              f"{os.path.relpath(PUBLISHED_LOG, HERE)} (для живой аналитики).")
        return
    print("Жду реальный результат (200 — это ещё не публикация)...")
    result = wait_done(key, rid)
    # Сохраняем связь с request_id независимо от исхода опроса: даже при timeout
    # пост мог выйти, и аналитике нужен именно request_id, чтобы это проверить.
    persist_published(rid, plats, user, material, media_type,
                      result.get("status") or "unknown", trial=a.trial,
                      results=result.get("results"))
    report(result)
    print(f"\nСвязь request_id сохранена в "
          f"{os.path.relpath(PUBLISHED_LOG, HERE)} (для живой аналитики).")

    # ⚠️ ГЛАВНОЕ. Раньше выход отсюда был всегда нулевым, и отказ площадки
    # уезжал в отчёт выпуска как «ОК». Теперь исход решает verdict().
    code, why = verdict(result)
    if code == EXIT_REJECTED:
        print(f"\nПУБЛИКАЦИЯ НЕ УДАЛАСЬ: {why}")
        print("Разберитесь с причиной и повторите эту ногу — дубля не будет, "
              "поста ведь нет.")
        sys.exit(code)
    if code != EXIT_OK:
        print(f"\nИСХОД НЕ ПОДТВЕРЖДЁН: {why}")
        sys.exit(code)


if __name__ == "__main__":
    main()
