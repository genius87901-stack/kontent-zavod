# -*- coding: utf-8 -*-
"""ПУБЛИКАЦИЯ ЧЕРЕЗ SmmBox. ОТДЕЛЬНЫЙ ПУТЬ, рядом с Upload-Post.

ЗАЧЕМ ОТДЕЛЬНЫЙ СЕРВИС, А НЕ ЕЩЁ ОДНА ПЛОЩАДКА В autopost.py.
Началось с одной дырки: личная стена ВК — единственное, чего мы не смогли
получить своими руками. Замеры лежат в VK_RESEARCH.md и vk_support_request.txt:
наше VK ID-приложение 54689226 на `wall.post` отвечает 1051, на
`photos.getWallUploadServer` — 15, а в выданном scope возвращается только
`vkid.personal_info offline`, сколько бы прав мы ни запрашивали. Права
`wall`/`photos` ВК новым приложениям не выдаёт. У SmmBox приложение с одобренным
`wall` есть давно — поэтому личная стена у них просто работает. Мы не обходим
ограничение ВК, мы пользуемся чужим одобрением.

Но адресаты приезжают из `/api/v1/groups`, то есть доступно ВСЁ, что клиент
подключил у себя в кабинете SmmBox, а не одна стена. Поэтому скрипт с 23.08.2026
умеет любую подключённую площадку из списка SOCIALS ниже. Список взят из
спецификации (enum `Social`), а не из нашей головы: чего в enum нет, того здесь
нет тоже.

ПОЧЕМУ ЭТО НЕ СЛИЛИ С autopost.py. Разные сервисы, разные протоколы, разные
деньги и разный радиус поражения. Upload-Post ведёт Instagram/YouTube/Threads/
Telegram и работает; ронять его ради ВК-стены нельзя. Общего здесь — только
слово «публикация», поэтому файлы соседи, а не родственники. Пересечение по
площадкам (Instagram, TikTok, Telegram, Facebook, X, Pinterest, YouTube) —
не повод объединять: у одного сервиса они уже проверены боем, у другого нет.

ЧТО НУЖНО И ГДЕ ЛЕЖИТ:
    SMMBOX_API_KEY   ключ из кабинета smmbox.com — СЕКРЕТ, лежит в `.env`
Читаем через modules/publishing/config_reader.py — тем же ридером, что автопостинг
и доктор, чтобы не разойтись с мастером установки. Ключ в чат не присылать.

ТАРИФ. Публикация через API у SmmBox входит в тариф ЭКСПЕРТ (ВИП) и выше; на
СТАРТ и ПРОФИ её нет вовсе. То есть клиенту нужен ПЛАТНЫЙ тариф стороннего
сервиса, и об этом сказано в вопросе мастера установки. Ключ на младшем тарифе
выдаётся — отказ приходит уже на публикации.

ЗАГОЛОВОК АВТОРИЗАЦИИ — ключ идёт со словом Bearer:
    Authorization: Bearer <ключ>
(У Upload-Post там Apikey, у Pexels ключ голый. Три сервиса — три формы.
Не перепутать.)

ПРОТОКОЛ (спецификация: https://smmbox.com/api/spec/openapi.yaml):
    GET  /api/v1/groups           кто подключён
    POST /api/v1/upload?type=...  файл (photo | video | doc)
    POST /api/v1/posts/postpone   публикация; тело {"posts": [...]}
Публикация двухшаговая: сначала файл уезжает на их хостинг и возвращает ссылку,
потом ссылка идёт вложением в пост. Одним запросом нельзя.

ВЛОЖЕНИЯ. Поле со ссылкой у фото и у видео РАЗНОЕ: `AttachPhoto.original`,
`AttachVideo.url`. Перепутать легко, а отвечает сервис на это «успехом» с пустым
постом. Держим их врозь и проверяем тестом.

АДРЕСАТ. В `group` кладём {"id": ..., "social": ..., "type": ...}. Скрипт не
принимает id на веру: он спрашивает /groups и сверяет, что такой адресат
действительно подключён, иначе пост уйдёт в никуда с ответом «успех». Если
подходящих адресатов больше одного — отказ с готовой командой, а не выбор
наугад.

ВИДЕО НА СТЕНЕ ВК ≠ КЛИП. Вертикальное видео ВК по умолчанию перекладывает в
Клипы — тогда записи на стене не будет. За это отвечает настройка
`no_transform_vk2clip`, и по умолчанию мы её ШЛЁМ: раз просили «на стену»,
значит на стену. Клип (`reels`) — осознанный выбор через --clip. Настройка
именно вэкашная, поэтому в другие соцсети она не уходит.

ПРЕДОХРАНИТЕЛЬ. По умолчанию скрипт НИЧЕГО НЕ ПУБЛИКУЕТ: он показывает, что
именно ушло бы. Отправка — только с явным --send. Причина та же, что у
autopost.py, и здесь она весомее: это может быть личная страница живого
человека. Публикация необратима и видна подписчикам. Удалить пост можно,
развидеть — нет. Публикуем только по явному слову владельца в текущем окне, а не
по «мы же договорились» из прошлой сессии.

КОДЫ ВОЗВРАТА: 0 — вышло, 1 — отказ (поста нет), 3 — исход не подтверждён.
Их читает release.py, чтобы не выдавать провал за успех.

СЕРВИС — ПЛАНИРОВЩИК, А НЕ КНОПКА «ОПУБЛИКОВАТЬ». Немедленной публикации у него
нет вовсе: на дату «сейчас» приходит отказ smmbox_18 «Вы указали дату, которая
уже прошла» — пока летит запрос и грузится видео, наше «сейчас» становится
прошлым. Поэтому «сейчас» здесь означает «через NOW_BUFFER секунд».

ВРЕМЯ. Поле `date` обязательно и меряется в unix timestamp. Спецификация
помечает его «(МСК)», а машина завода живёт в UTC+4 (Самара) — на час восточнее
Москвы. Шлём НАСТОЯЩИЙ unix timestamp (он от часового пояса не зависит) и
печатаем, что сервис вернул обратно: если он поймёт дату иначе, расхождение
будет видно сразу, а не всплывёт когда-нибудь на отложенных постах.

ЗАПУСК:
    python smmbox_post.py --list
    python smmbox_post.py --video out_reels/r1.mp4 --text-file caption_r1.txt
    python smmbox_post.py --video out_reels/r1.mp4 --social vk --type user --send
    python smmbox_post.py --photo out_c9/1.jpg --social tg --send
    python smmbox_post.py --dir out_c44 --text-file caption_c44.txt \
        --social vk --type user            # КАРУСЕЛЬ: все слайды одной записью

ТЕСТЫ: python run_tests.py --only smmbox
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Нет библиотеки requests. Установите: pip install requests==2.32.5")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "modules"))
try:
    from publishing import config_reader
except Exception:
    config_reader = None

API = "https://smmbox.com/api/v1"
TIMEOUT = 180
KEY_NAME = "SMMBOX_API_KEY"

#: ПЛОЩАДКИ — РОВНО enum `Social` ИЗ СПЕЦИФИКАЦИИ, буква в букву.
#: Двухсимвольные коды придумали не мы, и дописывать сюда «ещё одну соцсеть,
#: которая наверняка есть» НЕЛЬЗЯ: сервис ответит отказом на неизвестный код, а
#: человек будет искать ошибку у себя. Список сверен со спецификацией
#: https://smmbox.com/api/spec/openapi.yaml 23.08.2026.
#: Отдельно: в рекламной статье SmmBox упоминается ещё и Дзен, но кода для него
#: в enum НЕТ — значит через API он недоступен, и мы его не обещаем.
SOCIALS = {
    "vk": "ВКонтакте",
    "ok": "Одноклассники",
    "fb": "Facebook",
    "tg": "Telegram",
    "tw": "X (бывший Twitter)",
    "pi": "Pinterest",
    "tr": "Tumblr",
    "io": "Instagram",
    "to": "TikTok",
    "gg": "YouTube",
    "ry": "RuTube",
    "mx": "MAX",
}

#: Тип адресата — enum `Group.type` из спецификации.
TYPES = {
    "user": "личная страница",
    "group": "сообщество",
    "page": "публичная страница",
}

#: Площадки, куда пост БЕЗ файла не имеет смысла: это ленты картинок и видео.
#: Это НАШЕ знание о площадках, а не строка спецификации — сервис такой проверки
#: не делает и ответит «успехом» на пустой пост. Отдельная проверка нужна, чтобы
#: человек узнал о пустой публикации здесь, а не через день в ленте.
MEDIA_REQUIRED = {"io", "to", "gg", "ry", "pi"}

#: «Опубликовать сейчас» = запланировать на две минуты вперёд.
#: SmmBox — ПЛАНИРОВЩИК, кнопки «опубликовать немедленно» у него нет: на дату
#: «сейчас» он отвечает отказом smmbox_18 «Вы указали дату, которая уже прошла»
#: (проверено живьём 23.08.2026). Пока запрос летит и файл загружается, наше
#: «сейчас» успевает стать прошлым. Две минуты — запас, которого хватает на
#: загрузку тяжёлого видео и расхождение часов, и который человек не замечает.
NOW_BUFFER = 120

OK, REFUSED, UNCONFIRMED = 0, 1, 3


def _настоящий_запрос(method, url, **kw):
    """ЕДИНСТВЕННАЯ дверь этого скрипта наружу.

    Вынесена отдельной функцией не ради красоты: тесты подменяют ИМЕННО ЕЁ и
    после этого доказывают, что ни один настоящий запрос не ушёл. Пока она на
    месте, скрипт под предохранителем тестов отказывается работать вовсе (см.
    _sandbox_guard) — тот же приём, что у стенда генерации.
    """
    return requests.request(method, url, **kw)


_request = _настоящий_запрос


def _sandbox_guard():
    """Под тестами наружу не ходим — даже если ключ достали из .env мимо окружения.

    tests_guard/sitecustomize.py чистит ОКРУЖЕНИЕ и глушит сокеты, но ключ мы
    читаем из файла, то есть мимо чистки. Явный отказ по метке песочницы — тот
    же приём, что у стенда генерации: пусть отказ будет объяснимым, а не
    оборванным сокетом.

    Отказ снимается ровно одним способом — подменой двери наружу. Это не
    «галочка для тестов»: подменил транспорт — доказал, что публикация физически
    невозможна, и только тогда получил право звать main().
    """
    if os.environ.get("KARUSEL_TESTS_SANDBOX") == "1" and _request is _настоящий_запрос:
        sys.exit("ОТКАЗ: прогон под предохранителем тестов, а дверь наружу НЕ "
                 "подменена. Настоящих публикаций здесь не бывает.")


def api_key() -> str:
    key = None
    if config_reader is not None:
        try:
            key = config_reader.value(KEY_NAME, start=HERE)
        except Exception:
            key = None
    if not key:
        key = os.environ.get(KEY_NAME)
    if not key:
        sys.exit(
            "ПУБЛИКАЦИЯ ОСТАНОВЛЕНА — нет ключа сервиса SmmBox.\n"
            "\n"
            "Что это. Код доступа из личного кабинета smmbox.com: раздел\n"
            "«Настройки аккаунта» (smmbox.com/dashboard/auth-settings/).\n"
            "Публикация через их API работает с тарифа ЭКСПЕРТ (ВИП) и выше —\n"
            "на СТАРТ и ПРОФИ её нет, ключ выдадут, а пост не уйдёт.\n"
            "\n"
            "Куда положить: строкой  SMMBOX_API_KEY=<ключ>  в файл\n"
            f"    {HERE / '.env'}\n"
            "Либо пройдите мастер установки — он спросит и разложит сам."
        )
    return key.strip()


def call(method, path, *, key, params=None, payload=None, files=None):
    """Один запрос к SmmBox. Возвращает (вышло_ли, данные-или-описание-ошибки).

    Сервис отвечает 200 и на успех, и на прикладную ошибку, отличая их полем
    `success`. Поэтому по коду ответа судить нельзя — смотрим тело.
    """
    url = f"{API}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        r = _request(method, url, headers=headers, params=params,
                     json=payload, files=files, timeout=TIMEOUT)
    except requests.RequestException as e:
        return False, f"связь оборвалась: {type(e).__name__}: {e}"
    try:
        data = r.json()
    except ValueError:
        return False, f"HTTP {r.status_code}, ответ не JSON: {r.text[:300]}"
    if not isinstance(data, dict) or not data.get("success"):
        err = (data or {}).get("error") or {}
        detail = err.get("message") or json.dumps(data, ensure_ascii=False)[:300]
        return False, f"HTTP {r.status_code}, сервис отказал: {err.get('code', '?')} — {detail}"
    return True, data.get("response")


def groups(key):
    ok, res = call("GET", "groups", key=key)
    if not ok:
        sys.exit(f"Не удалось получить список подключённых страниц.\n{res}")
    return res or []


def social_name(code) -> str:
    """Человеческое имя площадки. Незнакомый код возвращаем как есть.

    Незнакомый — не выдумываем: если сервис однажды подключит площадку, которой
    нет в нашем SOCIALS, человек увидит её код, а не «неизвестно».
    """
    return SOCIALS.get(code, f"площадка «{code}»")


def type_name(code) -> str:
    return TYPES.get(code, f"тип «{code}»")


def describe(g) -> str:
    """Строка адресата для человека — вместе с готовой командой для него."""
    return (f"{social_name(g.get('social'))} — {type_name(g.get('type'))} "
            f"«{g.get('name')}» "
            f"(--social {g.get('social')} --type {g.get('type')} "
            f"--group-id {g.get('id')})")


def pick_target(key, want_id=None, social=None, gtype=None, found=None):
    """Адресат — только из живого списка /groups, а не из нашей веры в него.

    Ни один из фильтров не обязателен: если у клиента подключена одна страница,
    команда обходится без них вовсе. Но как только подходящих больше одной —
    отказ, а не выбор наугад: цена ошибки здесь — пост не на той странице.
    """
    found = groups(key) if found is None else found
    same = [g for g in found
            if (social is None or g.get("social") == social)
            and (gtype is None or g.get("type") == gtype)
            and (want_id is None or str(g.get("id")) == str(want_id))]
    if not same:
        чего_искали = []
        if social is not None:
            чего_искали.append(social_name(social))
        if gtype is not None:
            чего_искали.append(type_name(gtype))
        if want_id is not None:
            чего_искали.append(f"id {want_id}")
        подключено = "\n".join("  " + describe(g) for g in found) or "  ничего"
        sys.exit(
            "В кабинете SmmBox не подключён такой адресат"
            + (f" ({', '.join(чего_искали)})" if чего_искали else "") + ".\n"
            "Подключено сейчас:\n" + подключено + "\n"
            "Подключают страницы в кабинете smmbox.com, а не отсюда."
        )
    if len(same) > 1:
        sys.exit("Подходящих адресатов несколько — назовите нужного точнее:\n"
                 + "\n".join("  " + describe(g) for g in same))
    return same[0]


def upload(key, path, kind):
    """Файл на хостинг SmmBox. Возвращает словарь со ссылкой.

    `kind` — ровно то, что принимает /upload: photo | video | doc.
    """
    if not path.is_file():
        sys.exit(f"Файла нет: {path}")
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"   загружаю {path.name} ({size_mb:.1f} МБ, тип {kind}) ...")
    with open(path, "rb") as f:
        ok, res = call("POST", "upload", key=key, params={"type": kind},
                       files={"file": (path.name, f)})
    if not ok:
        sys.exit(f"Файл не загрузился.\n{res}")
    return res or {}


def build_options(social, media_kind, as_clip, vk_auto=False, as_story=False):
    """Настройки поста. Пусто — тоже честный ответ.

    Единственная настройка, которую мы шлём сами, — вэкашная
    `no_transform_vk2clip`: без неё вертикальное видео уезжает в Клипы и записи
    на стене не появляется. В другие соцсети она не уходит — там её смысла нет.
    `reels` («опубликовать в короткие видео») — общая, поэтому --clip работает
    везде, где у площадки такие видео есть.

    СОСТОЯНИЙ У ВИДЕО В ВК ТРИ, А НЕ ДВА. Долго было доступно два, и оба
    оказались тупиком, когда владелец попросил «клип И на стене И с полной
    подписью» (25.08.2026):
      `reels`                  — Клип, но сервис ОТКАЗЫВАЕТ на любом тексте:
                                 `smmbox_22 — Пост в Клипы ВК не может
                                 содержать текста` (живой отказ, HTTP 200);
      `no_transform_vk2clip`   — запись на стене с текстом, в Клипы не попадает;
      пусто (`vk_auto`)        — решает сам ВК: вертикальное видео он
                                 перекладывает в Клипы, а `reels` мы не шлём,
                                 значит и запрет на текст не срабатывает.
    Третье состояние из командной строки было недостижимо вовсе — отсюда флаг
    `--vk-auto`. Что именно ВК показывает на стене при авто-перекладывании,
    проверяется только глазами: у SmmBox нет метода чтения постов.
    """
    # СТОРИС — опция `stories` («Опубликовать в истории») из enum PostOption.
    # Проверяется ДО отсечки «только видео» ниже: сторис-картинка иначе ушла бы
    # обычной записью.
    #
    # ⚠️ ЖИВЬЁМ НЕ РАБОТАЕТ, ПРИЧИНА НЕ ВЫЯСНЕНА. Замер 28.08.2026, две попытки:
    #   сообщество 105689605 (id 670113588) -> ни истории, ни записи на стене
    #   личная страница 6720515 (id 670115266) -> то же самое
    # Оба задания сервис ПРИНЯЛ с номером и датой. Отказ выглядит как успех —
    # читать посты у SmmBox нечем (шесть методов, чтения нет). Диагностируется
    # только в их кабинете. Пока не подтверждено — сторис ВК идут прежним путём
    # (modules/story_publisher, ключ VK_USER_TOKEN_KATE), и release.py этот флаг
    # НЕ использует. Не выдёргивать тот путь, пока здесь не появится замер.
    #
    # ОТДЕЛЬНО, ЧТОБЫ НЕ ПОВТОРИТЬ: совпадение по времени доказательством НЕ
    # является. В 21:52 на странице появилась история через три минуты после
    # нашей отправки — и она оказалась чужой, выложенной руками. Поймали только
    # тем, что скачали картинку и посмотрели на неё.
    if as_story:
        return ["stories"]
    if media_kind != "video":
        return []
    if as_clip:
        return ["reels"]
    if social == "vk":
        return [] if vk_auto else ["no_transform_vk2clip"]
    return []


#: Потолок слайдов в карусели — правило ВКонтакте, не сервиса. Спецификация
#: SmmBox числа вложений не ограничивает вовсе (сверено 28.08.2026), но ВК
#: больше десяти фото в одной записи не берёт. Столько же проверяет vkcarousel.py.
MAX_PHOTOS = 10


def slides_in(dirname):
    """Слайды карусели из папки: .png/.jpg по алфавиту. Как в vkcarousel.py."""
    src = Path(dirname)
    if not src.is_absolute():
        src = HERE / src
    if not src.is_dir():
        sys.exit("Нет папки " + str(src))
    imgs = sorted(p for p in src.glob("*")
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not imgs:
        sys.exit("В папке нет .png/.jpg: " + str(src))
    return imgs


def build_post(target, *, text, media, media_kind, when, as_clip, autodelete_min,
               vk_auto=False, video_desc=None, video_title=None,
               posting_mode=None, as_story=False):
    """Тело поста строго по схемам спецификации.

    ССЫЛКА У ФОТО И ВИДЕО ЛЕЖИТ В РАЗНЫХ ПОЛЯХ: AttachPhoto — `original`,
    AttachVideo — `url`. Это не наша прихоть, это две разные схемы.

    ОПИСАНИЕ ВИДЕО — ОТДЕЛЬНОЕ ПОЛЕ, И ЭТО НЕ ПРИДИРКА. Текст поста (вложение
    `text`) достаётся ЗАПИСИ НА СТЕНЕ, а Клип ВК берёт подпись из описания
    САМОГО ВИДЕО. Поэтому пока мы слали только `text`, клип выходил немым:
    владелец увидел живьём 25.08.2026 — «стена есть, клип есть, но на клипе без
    описания». Шлём `description` вместе с текстом, чтобы подпись была в обоих
    местах.

    ⚠️ ЗДЕСЬ РАНЬШЕ СТОЯЛА НЕПРАВДА, СТОИВШАЯ НАМ КЛИПОВ БЕЗ ПОДПИСИ: «у
    AttachVideo в спецификации всего три свойства (url, preview, duration)».
    Сверено с живой спецификацией (https://smmbox.com/api/spec/openapi.yaml)
    25.08.2026: у AttachVideo есть и `title`, и `description`, и `width`/
    `height`, и `size`/`ext`, и privacy-поля. Правило «лишних полей не
    досылаем» остаётся, но проверять его надо ПО СПЕЦИФИКАЦИИ, а не по этой
    заметке.
    """
    attachments = []
    if text:
        attachments.append({"type": "text", "text": text, "is_html": 0})
    # НЕСКОЛЬКО ФОТО = КАРУСЕЛЬ. На стене ВК запись с несколькими фото и есть
    # свайп-альбом; отдельного типа «карусель» у сервиса нет. `AttachAlbum` из
    # спецификации — НЕ ОНО: он требует id УЖЕ существующего альбома ВК и
    # прикрепляет ссылку на него, а не собирает пост из загруженных слайдов.
    # Ограничения на число вложений спецификация не объявляет (сверено
    # 28.08.2026); потолок в 10 — это правило самого ВК, см. MAX_PHOTOS.
    # media принимает и один загруженный файл, и список — ради вызовов из тестов.
    for item in ([] if not media else (media if isinstance(media, list) else [media])):
        if media_kind == "photo":
            att = {"type": "photo", "original": item["url"]}
            for k in ("width", "height"):
                if item.get(k) is not None:
                    att[k] = item[k]
        else:
            att = {"type": "video", "url": item["url"]}
            if item.get("duration") is not None:
                att["duration"] = item["duration"]
            # Подпись клипа живёт здесь, а не в тексте поста — см. докстринг.
            if video_desc:
                att["description"] = video_desc
            if video_title:
                att["title"] = video_title
        attachments.append(att)

    post = {
        "date": when,
        "group": {"id": target["id"], "social": target["social"],
                  "type": target["type"]},
        "attachments": attachments,
    }
    # КАК ВК ПОКАЗЫВАЕТ НЕСКОЛЬКО ФОТО. По умолчанию — СЕТКОЙ (`grid`): девять
    # картинок плиткой, листать нечего. Замер 28.08.2026, живой пост
    # vk.com/wall-105689605_19652: девять вложений `photo` в одной записи ушли
    # плиткой, потому что это поле мы не слали вовсе. Листаемая карусель — это
    # `properties.posting_mode = "carousel"` из спецификации, и ничто иное:
    # количество вложений на вид записи НЕ влияет.
    if posting_mode:
        post["properties"] = {"posting_mode": posting_mode}
    options = build_options(target.get("social"), media_kind if media else None,
                            as_clip, vk_auto, as_story)
    if options:
        post["options"] = options
    if autodelete_min:
        post["autodelete"] = {"hours": autodelete_min // 60,
                              "minutes": autodelete_min % 60}
    return post


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Публикация через SmmBox в любую подключённую соцсеть.",
        epilog="Площадки: " + ", ".join(f"{k} — {v}" for k, v in SOCIALS.items()))
    ap.add_argument("--video", type=Path, help="файл видео")
    ap.add_argument("--photo", type=Path, action="append", default=[],
                    metavar="ФАЙЛ",
                    help="файл изображения; можно повторить — выйдет карусель")
    ap.add_argument("--dir", type=Path, metavar="ПАПКА",
                    help="папка со слайдами карусели (.png/.jpg по алфавиту)")
    ap.add_argument("--story", action="store_true",
                    help="опубликовать в истории (сторис) — ⚠ ЖИВЬЁМ НЕ "
                         "ПОДТВЕРЖДЕНО, см. комментарий у build_options")
    ap.add_argument("--grid", action="store_true",
                    help="показать слайды СЕТКОЙ, а не листаемой каруселью "
                         "(по умолчанию несколько фото в ВК идут каруселью)")
    ap.add_argument("--text", default="", help="текст записи")
    ap.add_argument("--text-file", type=Path, help="взять текст из файла (UTF-8)")
    ap.add_argument("--social", choices=sorted(SOCIALS),
                    help="площадка: " + ", ".join(f"{k} — {v}" for k, v in SOCIALS.items())
                         + ". Без неё берётся единственная подключённая.")
    ap.add_argument("--group-id", help="id адресата, если подключено несколько")
    ap.add_argument("--type", choices=sorted(TYPES),
                    help="user — личная страница, group — сообщество, "
                         "page — публичная страница")
    ap.add_argument("--at", type=int, help="unix timestamp отложенной публикации")
    ap.add_argument("--in-min", type=int, help="опубликовать через N минут")
    ap.add_argument("--clip", action="store_true",
                    help="короткое видео (Клипы ВК, Reels, Shorts). По умолчанию "
                         "видео остаётся обычной записью")
    ap.add_argument("--video-title", metavar="СТРОКА",
                    help="название видео (поле title вложения)")
    ap.add_argument("--no-video-desc", action="store_true",
                    help="НЕ класть текст поста в описание видео. По умолчанию "
                         "кладём: иначе Клип ВК выходит без подписи")
    ap.add_argument("--vk-auto", action="store_true",
                    help="ВК: не навязывать режим — пусть сам переложит "
                         "вертикальное видео в Клипы. В отличие от --clip "
                         "текст при этом НЕ запрещён")
    ap.add_argument("--autodelete", type=int, metavar="МИН",
                    help="удалить пост через N минут — для проверок")
    ap.add_argument("--list", action="store_true",
                    help="показать подключённые страницы и выйти")
    ap.add_argument("--send", action="store_true",
                    help="ОТПУБЛИКОВАТЬ ПО-НАСТОЯЩЕМУ. Без него — только показать.")
    args = ap.parse_args(argv)

    # Отказ ДО первого запроса, а не перед отправкой: этот скрипт без сети
    # бесполезен целиком — даже --list идёт наружу. Пусть под тестами он
    # объясняет отказ словами, а не падает оборванным сокетом.
    _sandbox_guard()

    key = api_key()

    if args.list:
        найдено = groups(key)
        if not найдено:
            print("В кабинете SmmBox не подключено ни одной страницы.")
            print("Подключают их на smmbox.com, а не отсюда.")
            return OK
        print("ПОДКЛЮЧЕНО В КАБИНЕТЕ SmmBox:")
        for g in найдено:
            print("  " + describe(g))
        return OK

    # ФОТО МОЖЕТ БЫТЬ НЕСКОЛЬКО — это и есть карусель (см. build_post).
    photos = list(args.photo or [])
    if args.dir:
        photos = slides_in(args.dir) + photos
    if args.video and photos:
        sys.exit("--video и фото вместе не бывает: пост либо с видео, либо с фото.")
    if len(photos) > MAX_PHOTOS:
        sys.exit(f"В карусели ВК максимум {MAX_PHOTOS} фото, дано {len(photos)}.")

    text = args.text
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8-sig").strip()
    # Клип ВК берёт подпись из описания видео, а не из текста поста. По
    # умолчанию кладём туда тот же текст — иначе клип выходит немым.
    video_desc = None if args.no_video_desc else (text or None)
    media_paths = photos or ([args.video] if args.video else [])
    media_path = media_paths[0] if media_paths else None
    media_kind = "photo" if photos else ("video" if args.video else None)
    if not text and not media_path:
        sys.exit("Нечего публиковать: нужен --video, --photo и/или --text.")

    target = pick_target(key, args.group_id, args.social, args.type)
    соц = target.get("social")
    print(f"АДРЕСАТ: {describe(target)}")

    if not media_path and соц in MEDIA_REQUIRED:
        sys.exit(f"{social_name(соц)} не принимает запись без файла — это лента "
                 f"картинок и видео. Добавьте --photo или --video.")

    if args.at:
        when = args.at
        if when <= int(time.time()):
            sys.exit(f"Дата {time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(when))} "
                     f"уже прошла — SmmBox такие не принимает (он планировщик, а не "
                     f"кнопка «опубликовать»). Укажите время в будущем.")
    elif args.in_min:
        when = int(time.time()) + args.in_min * 60
    else:
        when = int(time.time()) + NOW_BUFFER
    print(f"ВРЕМЯ:   {when} -> по часам этой машины "
          f"{time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(when))}")
    режим = "ИСТОРИЯ (сторис)" if args.story else (
        "КОРОТКОЕ ВИДЕО" if args.clip else (
        "НА УСМОТРЕНИЕ ВК (сам переложит в Клипы)"
        if (соц == "vk" and args.vk_auto) else
        "ЗАПИСЬ НА СТЕНЕ" if соц == "vk" else "ОБЫЧНАЯ ЗАПИСЬ"))
    print(f"РЕЖИМ:   {режим}"
          + (f", автоудаление через {args.autodelete} мин" if args.autodelete else ""))
    # Несколько фото в ВК по умолчанию показываются СЕТКОЙ. Листаемую карусель
    # включает только properties.posting_mode — см. комментарий в build_post.
    posting_mode = None
    if соц == "vk" and media_kind == "photo" and len(media_paths) > 1:
        posting_mode = "grid" if args.grid else "carousel"
    if media_kind == "photo" and len(media_paths) > 1:
        как = {"carousel": "листаемой каруселью", "grid": "сеткой (плиткой)"}.get(
            posting_mode, "как решит площадка")
        print(f"СЛАЙДОВ: {len(media_paths)} — покажутся {как}")
        for i, p in enumerate(media_paths, 1):
            print(f"   {i}. {p}")
    elif media_path:
        print(f"ФАЙЛ:    {media_path} ({media_kind})")
    if text:
        print(f"ТЕКСТ:   {text[:200]}{'...' if len(text) > 200 else ''}")

    if not args.send:
        print("\n--- ПРЕДОХРАНИТЕЛЬ: НИЧЕГО НЕ ОПУБЛИКОВАНО ---")
        print("Файл не загружался, пост не создавался. Для настоящей публикации "
              "добавьте --send.")
        return OK

    # Каждый слайд грузится своим запросом: одним нельзя, у сервиса
    # POST /upload принимает один файл и возвращает одну ссылку.
    media = []
    for n, p in enumerate(media_paths, 1):
        got = upload(key, p, media_kind)
        media.append(got)
        размеры = f"{got.get('width')}x{got.get('height')}" \
            if got.get("width") else f"{got.get('duration')} сек"
        подпись = f"   загружено {n}/{len(media_paths)}: {размеры}" \
            if len(media_paths) > 1 else f"   загружено: {размеры}"
        print(подпись)
    media = media or None

    post = build_post(target, text=text, media=media, media_kind=media_kind,
                      when=when, as_clip=args.clip, autodelete_min=args.autodelete,
                      vk_auto=args.vk_auto, video_desc=video_desc,
                      video_title=args.video_title, posting_mode=posting_mode,
                      as_story=args.story)
    ok, res = call("POST", "posts/postpone", key=key, payload={"posts": [post]})
    if not ok:
        print(f"\nПУБЛИКАЦИЯ НЕ СОСТОЯЛАСЬ.\n{res}")
        return REFUSED

    posts = (res or {}).get("posts") or []
    links = (res or {}).get("links") or []
    if not posts:
        print("\nСервис ответил успехом, но не вернул ни одного поста — исход "
              "неясен. Проверьте ленту и кабинет SmmBox вручную.")
        print(json.dumps(res, ensure_ascii=False, indent=2)[:1000])
        return UNCONFIRMED

    print("\nПРИНЯТО В РАСПИСАНИЕ SmmBox (это ещё не запись в ленте):")
    for p in posts:
        got = p.get("date")
        когда = time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(got)) if got else "?"
        print(f"  id {p.get('id')}, дата {got} ({когда})")
        if got and abs(got - when) > 60:
            print(f"  ВНИМАНИЕ: сервис сдвинул время на {(got - when) / 3600:+.1f} ч — "
                  f"значит `date` он понимает не как чистый unix timestamp. "
                  f"Учесть в отложенных постах.")
    for link in links:
        print(f"  ссылка: {link}")
    if not links:
        print("  Ссылки сервис не вернул — публикация могла встать в очередь. "
              "Проверьте ленту.")
        return UNCONFIRMED

    # ПОЧЕМУ ЗДЕСЬ НЕТ ОПРОСА СТАТУСА, КАК У autopost.py. У Upload-Post есть
    # GET /status?request_id, и там мы дожидаемся completed. У SmmBox такого
    # метода нет вовсе: в спецификации ровно шесть путей, читать посты нечем.
    # Своими ключами ВК проверить стену тоже нельзя — VK_USER_TOKEN привязан к
    # чужому IP (ошибка 5), токен сообщества чужую стену не читает (ошибка 27).
    # Значит честная граница нашего знания — «сервис принял». Код 0 означает
    # ИМЕННО ЭТО, и печатаем мы то же самое, чтобы никто не прочитал успех
    # отправки как запись в ленте.
    print("\nПОДТВЕРДИТЬ ПУБЛИКАЦИЮ ОТСЮДА НЕЧЕМ: у SmmBox нет метода чтения "
          "постов. Откройте ленту и убедитесь глазами.")
    return OK


if __name__ == "__main__":
    sys.exit(main())
