# -*- coding: utf-8 -*-
"""
ОЧЕРЕДЬ ПУБЛИКАЦИЙ — выпуск по расписанию, но ТОЛЬКО того, что одобрил человек.

Единицы кладутся в очередь с временем выхода, а задание Планировщика Windows
(на маке — агент запуска launchd) раз в 5 минут дёргает `--run`. Планировщика в
самом заводе нет (см. программу созвона 3), поэтому расписание живёт здесь —
см. installer/schedule_publisher.py, он ставит обе стороны.

ГЛАВНЫЙ ЗАКОН ПРОЕКТА (уточнён 23.08.2026 прямым решением владельца):
ВРЕМЯ САМО ПО СЕБЕ НИЧЕГО НЕ ПУБЛИКУЕТ. Условий на выход ровно два, и они
складываются, а не заменяют друг друга:
    подошло время  И  запись одобрена ЧЕЛОВЕКОМ  →  публикуем;
    подошло время,  а одобрения нет              →  НЕ публикуем, говорим,
                                                    что ждём одобрения.

ЧТО БЫЛО ДО 23.08.2026 И ПОЧЕМУ ЭТО ЧИНИЛИ (замерено, а не пересказано).
`--add` БЕЗ `--hold` сам писал в запись `status: "approved"` — слово «одобрено»
ставила машина, человека при этом никто не спрашивал. Дальше `--run` видел
`approved`, время подошло, и публикация уходила. То есть тихая публикация была
поведением ПО УМОЛЧАНИЮ, а `--hold` — необязательной галочкой, о которой надо
было вспомнить. Клиентская же инструкция обещала «я никогда ничего не публикую
сам по себе». Обещание и поведение разошлись — выбрана сторона обещания.

СТАЛО: `--add` кладёт запись ТОЛЬКО в `pending` («ждёт вашего одобрения»), и
единственный способ получить `approved` — команда `--approve`, то есть действие
человека. В записи при этом проставляется `approved_at`; без этой отметки
`--run` считает запись НЕодобренной, даже если в поле `status` каким-то образом
оказалось слово `approved` (так закрыты записи, которые старая версия успела
пометить сама, и ручная правка json).

СУДЬБА НЕОДОБРЕННОЙ ЗАПИСИ — своя, отдельная. Она не выходит и не «протухает»
молча: пока идёт окно догона (`--catchup-hours`), `--run` печатает, что ждёт
одобрения; когда окно кончилось — запись получает статус `expired` («время
вышло, одобрения не было») и больше сама не выйдет НИКОГДА. Это НЕ то же, что
`missed`: `missed` — про одобренное, которое не смогли выпустить (компьютер был
выключен), `expired` — про неспрошенное согласие.

ПРЕДОХРАНИТЕЛЬ `--send` ОСТАЁТСЯ И НИКУДА НЕ ДЕВАЕТСЯ. Он уходит в КАЖДУЮ
дочернюю команду, и cmd_run() отказывается запускать команду, в которой его нет
(см. `SEND_GUARD`). Он НЕ заменяет одобрение, а дополняет его: одобрение
отвечает на вопрос «человек этого хотел?», `--send` — на вопрос «дочерний
скрипт точно понял, что это боевой запуск, а не проба?».

СТАТУСЫ ЗАПИСИ:
    pending   ждёт вашего одобрения  (сюда запись ложится при --add)
    approved  одобрено человеком, выйдет в своё время (стоит approved_at)
    done      опубликовано
    failed    ошибка при отправке
    missed    одобрено, но компьютер был выключен дольше окна догона
    expired   одобрения так и не было, время вышло — само не выйдет

ЧТО УЧТЕНО ПО ГРАБЛЯМ 09.08:
  * Telegram режет подпись на 1024 символах и заваливает ВЕСЬ запрос — поэтому
    TG всегда отправляется ОТДЕЛЬНЫМ вызовом и берёт короткую подпись
    (`caption_<имя>_threads.txt` / `_short.txt`), если она есть;
  * ВК идёт своим кодом (vkcarousel.py — свайп-альбом, vkpost.py — клип), а не
    через Upload-Post;
  * пропущенное при выключенном компьютере догоняется, но не бесконечно: запись
    старше `--catchup-hours` (по умолчанию 6) не выходит «задним числом», а
    помечается missed — иначе после двух выключенных суток лента получила бы
    десяток постов подряд.

ПРОБНЫЕ REELS (`--trial`, добавлено 17.08.2026 под пачку 17-23.08). Instagram
Trial Reels — сначала показ только не-подписчикам (нужен публичный аккаунт от
1000 подписчиков, иначе Instagram флаг молча проигнорирует и опубликует как
обычный Reels — см. autopost.py). Флаг кладётся В ЗАПИСЬ ОЧЕРЕДИ при `--add`
и на `--run` прокидывается ТОЛЬКО в instagram-заливку (телеграм/вк его не
знают): `auto` — сам разойдётся подписчикам, если наберёт просмотры за 72ч,
`manual` — остаётся у не-подписчиков, пока не решишь в приложении сама.

ПРИВЕДЕНО К КОНТРАКТУ КОРОБКИ (CONTRACT.md §3):
  * никаких абсолютных путей — всё через pathlib от корня установки, а корень
    ищется общим для проекта способом (install/factory_config.py, маркер
    `.factory_root`; CONTRACT.md в коробку НЕ едет — см. install_root());
  * никакого input() и интерактива: очередь дёргает Планировщик, а не человек;
  * целевая ОС — Windows, bash-измов нет;
  * дочерние скрипты запускаются НЕ словом `python` (на чистой Windows это
    заглушка Microsoft Store, см. install/stages.json), а конкретным
    интерпретатором коробки — см. python_exe();
  * часовой пояс НЕ захардкожен: берётся из настроек клиента, а если их нет —
    часовой пояс самого компьютера, по которому живёт и Планировщик Windows.
    Подробности — в timezone_name().

ЗАПУСК:
    python publish_queue.py --add out_c52 --at "2026-08-15 09:15" --to instagram,telegram,vk
    python publish_queue.py --add out_reels/ai1.mp4 --at "2026-08-17 14:00" --to instagram,telegram,vk --trial auto
    python publish_queue.py --list
    python publish_queue.py --approve out_c52     # ← без этого не выйдет НИЧЕГО
    python publish_queue.py --unapprove out_c52   # передумали: обратно в ожидание
    python publish_queue.py --run            # это дёргает Планировщик
    python publish_queue.py --run --dry-run  # показать, что ушло бы
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# Предохранитель проекта. Держим ОДНОЙ константой, чтобы его нельзя было
# «случайно» потерять в одной из трёх веток сборки команды: cmd_run() сверяет
# по ней каждую команду перед запуском.
SEND_GUARD = "--send"

# ── одобрение человеком ──────────────────────────────────────────────────────
# Статус, с которым запись ЛОЖИТСЯ в очередь. Другого начального статуса нет:
# машина не имеет права выписать себе разрешение на публикацию.
STATUS_NEW = "pending"
# Статус, который ставит ТОЛЬКО cmd_approve(), то есть человек своей командой.
STATUS_OK = "approved"
# Отметка «кто и когда сказал да». Её отсутствие делает слово «approved» в поле
# status недействительным — см. is_approved(). Так закрыты сразу два случая:
# записи, помеченные approved старой версией самого скрипта, и правка json
# руками мимо команды --approve.
APPROVED_MARK = "approved_at"

# Человеческие подписи статусов — их читает клиент в --list, а не программист.
STATUS_LABEL = {
    "pending": "ЖДЁТ ВАШЕГО ОДОБРЕНИЯ",
    "approved": "одобрено, выйдет по расписанию",
    "done": "опубликовано",
    "failed": "ошибка",
    "missed": "одобрено, но компьютер был выключен — не вышло",
    "expired": "одобрения не было, время вышло — само не выйдет",
}

# Статусы, по которым запись уже закрыта: --run их не смотрит вовсе.
STATUS_CLOSED = ("done", "failed", "missed", "expired")


def is_approved(item: dict) -> bool:
    """Одобрил ли эту запись ЧЕЛОВЕК. Единственная точка ответа на этот вопрос.

    Два условия, и оба обязательны: статус `approved` И отметка `approved_at`.
    Отметку проставляет только cmd_approve(). Одного статуса мало сознательно:
    до 23.08.2026 его писал сам `--add`, и такие записи выходили без спроса —
    теперь они читаются как неодобренные и ждут человека, а не ленту.
    """
    return (item.get("status") == STATUS_OK
            and bool(str(item.get(APPROVED_MARK) or "").strip()))

# ── корень установки ─────────────────────────────────────────────────────────
# Маркеры — те же и в том же порядке, что у install/factory_config.py.
# `.factory_root` первый: его кладёт установщик, и он есть в КАЖДОЙ клиентской
# коробке. CONTRACT.md в коробку НЕ едет (build_box его не возит, это документ
# разработки) — поэтому он идёт последним, только для дев-репозитория.
_ROOT_MARKERS = (".factory_root", "config/factory.config.json", "modules", "CONTRACT.md")

_ROOT_CACHE: list[Path] = []
_FC_CACHE: list = []          # [] — ещё не пробовали; [модуль|None] — пробовали
_TZ_OVERRIDE: list[str] = []  # значение --tz, если его назвали в командной строке


def _factory_config():
    """install/factory_config.py как модуль, БЕЗ правки sys.path.

    Канонический ридер настроек коробки лежит в install/ и на PYTHONPATH не
    попадает. Грузим по пути — ровно тем же приёмом, что engine4._factory_config().
    Файла нет (дев-запуск, движок вынут из коробки) — None, и очередь работает
    на запасных решениях, а не падает (CONTRACT.md §3).
    """
    if _FC_CACHE:
        return _FC_CACHE[0]
    here = Path(__file__).resolve().parent
    mod = None
    for cand in (here / "install" / "factory_config.py",
                 here.parent / "install" / "factory_config.py"):
        if not cand.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("_kz_factory_config", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:                                   # noqa: BLE001
            mod = None
        break
    _FC_CACHE.append(mod)
    return mod


def _root_by_markers() -> Path:
    """Запасной поиск корня — теми же маркерами, если factory_config недоступен."""
    env = os.environ.get("FACTORY_ROOT")
    if env and Path(env).exists():
        return Path(env).resolve()
    here = Path(__file__).resolve().parent
    for base in (here, Path.cwd()):
        for cand in (base, *base.parents):
            for marker in _ROOT_MARKERS:
                if (cand / marker).exists():
                    return cand
    return here


def install_root() -> Path:
    """Корень установки. Один способ на весь проект — общий ридер коробки.

    Было `HERE = os.path.dirname(os.path.abspath(__file__))`. Для дев-репозитория
    этого хватало, для коробки — нет: у клиента очередь может быть запущена из
    любой рабочей папки (Планировщик задаёт свою), а состояние и каптионы обязаны
    лежать в корне УСТАНОВКИ. factory_config.root() знает про FACTORY_ROOT и про
    `.factory_root`, и берёт самый глубокий маркер — это защищает от случая
    «коробку распаковали внутрь другой папки с modules/».
    """
    if _ROOT_CACHE:
        return _ROOT_CACHE[0]
    fc = _factory_config()
    found = None
    if fc is not None:
        try:
            found = Path(fc.root(start=Path(__file__).resolve().parent))
        except Exception:                                   # noqa: BLE001
            found = None
    _ROOT_CACHE.append(found or _root_by_markers())
    return _ROOT_CACHE[0]


def reset_caches() -> None:
    """Забыть найденный корень и загруженный конфиг.

    Нужно тесту: в одном процессе он проверяет несколько разных «коробок».
    В рабочем запуске зовётся ровно ноль раз.
    """
    _ROOT_CACHE.clear()
    _FC_CACHE.clear()
    _TZ_OVERRIDE.clear()


def queue_path() -> Path:
    return install_root() / ".state" / "publish_queue.json"


def log_path() -> Path:
    return install_root() / ".state" / "publish_queue.log"


def _config_value(key: str) -> str:
    """Ответ клиента из config/factory.config.json (или пусто)."""
    fc = _factory_config()
    if fc is None:
        return ""
    try:
        return str(fc.answer(key, "") or "").strip()
    except Exception:                                       # noqa: BLE001
        return ""


# ── часовой пояс ─────────────────────────────────────────────────────────────
# БЫЛО: MSK = dt.timezone(dt.timedelta(hours=3)) — Москва прибита гвоздями.
# Клиент из Екатеринбурга, поставив «выход в 09:15», получал бы выход в 11:15
# по своим часам, причём молча.
#
# СТАЛО — три источника по убыванию доверия, БЕЗ нового вопроса клиенту:
#   1. `--tz` в командной строке (имя зоны IANA или смещение «+05:00»);
#   2. ответ клиента в конфиге: `publish_timezone`, а если его нет —
#      `content_plan_timezone`. Второй мастер УЖЕ спрашивает
#      (modules/content_plan/module.manifest.json), и заводить второй вопрос про
#      то же самое — прямой путь к двум расходящимся ответам у одного человека;
#   3. часовой пояс САМОГО КОМПЬЮТЕРА. Это не «догадка»: очередь дёргает
#      Планировщик Windows, а он живёт по местному времени машины. Значит и
#      «выйти в 09:15» человек за этой машиной понимает как местные 09:15.
#
# ⚠️ ВНИМАНИЕ ВЛАДЕЛЬЦУ (замерено 22.08.2026). Часовой пояс ЭТОЙ машины —
# +04:00 («RTZ 3»), а не МСК, и config/factory.config.json на ней нет. То есть
# раньше очередь считала введённое время московским, а Планировщик будил её по
# местному: расхождение на час было И ДО этой правки, просто молча. Теперь
# очередь печатает свой пояс в --add и --list, и если выпуск должен идти по
# Москве — это надо сказать ЯВНО одним из двух способов:
#     python publish_queue.py --add ... --tz Europe/Moscow
#     либо положить "publish_timezone": "Europe/Moscow" в config/factory.config.json
# Записи, уже лежащие в очереди без поля tz, читаются текущей зоной выпуска —
# см. запасной вариант в cmd_run().
#
# Имя зоны разворачивается через zoneinfo, а если базы IANA нет (обычная
# ситуация на офлайн-Windows: stdlib-zoneinfo там требует пакет tzdata) —
# принимаем смещение вида «+05:00» и, в последнюю очередь, откатываемся на пояс
# компьютера. Молча ошибиться на часы очередь не имеет права, поэтому
# нераспознанное значение печатается человеческой строкой (см. tz_label()).
TZ_CONFIG_KEYS = ("publish_timezone", "content_plan_timezone")


def parse_offset(raw) -> dt.timezone | None:
    """«+03:00» / «+3» / «-05:30» / «UTC+4» → timezone. Не смещение → None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper().replace("UTC", "").replace("GMT", "").strip()
    if not s or s[0] not in "+-":
        return None
    sign = -1 if s[0] == "-" else 1
    s = s[1:]
    hh, _, mm = s.partition(":") if ":" in s else (s, "", "0")
    try:
        hours, minutes = int(hh), int(mm or 0)
    except ValueError:
        return None
    if not (0 <= hours <= 14 and 0 <= minutes < 60):
        return None
    return dt.timezone(sign * dt.timedelta(hours=hours, minutes=minutes))


def local_tz() -> dt.tzinfo:
    """Часовой пояс этого компьютера — тот же, по которому живёт Планировщик."""
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def timezone_name() -> str:
    """Имя/смещение зоны из --tz или настроек. Пусто = пояс компьютера."""
    if _TZ_OVERRIDE and _TZ_OVERRIDE[0]:
        return _TZ_OVERRIDE[0]
    for key in TZ_CONFIG_KEYS:
        value = _config_value(key)
        if value:
            return value
    return ""


def tz_info(name: str | None = None) -> dt.tzinfo:
    """tzinfo по имени зоны или смещению. Ничего не распознали — пояс машины."""
    name = (name or "").strip()
    if not name:
        return local_tz()
    offset = parse_offset(name)
    if offset is not None:
        return offset
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                                       # noqa: BLE001
        # Нет базы IANA (офлайн-Windows без tzdata) или опечатка в имени.
        return local_tz()


def tz_label(name: str | None = None) -> str:
    """Человеческая подпись зоны для печати: чтобы никто не гадал, чьи это часы."""
    name = (name or "").strip()
    now = dt.datetime.now(tz_info(name))
    offset = now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if offset else "?"
    if not name:
        return f"время этого компьютера ({offset})"
    if parse_offset(name) is not None:
        return f"{name} (смещение задано вручную)"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return f"{name} ({offset})"
    except Exception:                                       # noqa: BLE001
        return (f"зона «{name}» на этом компьютере не распознана "
                f"(нет базы часовых поясов) — берём время компьютера ({offset}). "
                f"Если это важно, задай смещение цифрами, например --tz +05:00")


def offset_str(when: dt.datetime, zone: dt.tzinfo) -> str:
    """Смещение зоны В МОМЕНТ `when` строкой «+03:00» (переход на лето учтён)."""
    delta = zone.utcoffset(when.replace(tzinfo=None)) or dt.timedelta(0)
    total = int(delta.total_seconds()) // 60
    sign = "-" if total < 0 else "+"
    return "%s%02d:%02d" % (sign, abs(total) // 60, abs(total) % 60)


def now_local() -> dt.datetime:
    return dt.datetime.now(tz_info(timezone_name()))


# ── чем запускать дочерние скрипты ───────────────────────────────────────────
def python_exe() -> str:
    """Интерпретатор для autopost.py / vkcarousel.py / vkpost.py.

    Словом `python` не запускаем НИКОГДА: на чистой Windows 10/11 оно всегда
    находится, но это заглушка Microsoft Store, а не питон (записано дословно в
    install/stages.json). Порядок — как в install/check_env.py
    (BOX_PYTHON_RELPATHS): сначала `.venv` коробки, потому что зависимости
    (requests, vk_api, Pillow) стоят именно там, и запуск встраиваемым
    `python\\python.exe` уронил бы публикацию на первом же импорте. Дальше — тот
    интерпретатор, которым запущена сама очередь, и лишь потом встраиваемый.
    """
    root = install_root()
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python3", ".venv/bin/python"):
        p = root.joinpath(*rel.split("/"))
        if p.is_file():
            return str(p)
    if sys.executable:
        return sys.executable
    embedded = root / "python" / "python.exe"
    if embedded.is_file():
        return str(embedded)
    raise SystemExit(
        "Не найден интерпретатор Python для запуска публикации.\n"
        "Ожидался .venv внутри папки завода (.venv\\Scripts\\python.exe). "
        "Что делать: попроси переустановить завод — окружение разворачивает "
        "мастер установки, руками его чинить не нужно.")


def load() -> dict:
    p = queue_path()
    if not p.exists():
        return {"items": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # Битый файл очереди не должен ронять задание Планировщика трассировкой.
        print("Файл очереди повреждён и прочитан быть не может: %s" % p)
        return {"items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"items": []}
    return data


def save(q: dict) -> None:
    p = queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")


def log(line: str) -> None:
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (now_local().strftime("%Y-%m-%d %H:%M"), line))


def item_id(target: str) -> str:
    """id записи = имя папки карусели или файла ролика без расширения."""
    return Path(str(target).replace("\\", "/").rstrip("/")).stem


def caption_files(target: str) -> tuple[Path | None, Path | None]:
    """Полная подпись и короткая (для Telegram). Имена — как сложилось в проекте:
    caption_<имя>.txt и caption_<имя>_threads.txt / _short.txt."""
    root = install_root()
    name = item_id(target)
    # Папка называется out_c48, а подпись — caption_c48.txt: префикс «out_» в
    # имена подписей никогда не входил. Без этого срезания очередь уходила бы
    # вообще без текста — молча, потому что autopost без --caption-file просто
    # берёт пустую подпись.
    names = [name]
    if name.startswith("out_"):
        names.append(name[4:])

    full = short = None
    for n in names:
        p = root / ("caption_%s.txt" % n)
        if p.exists():
            full = p
            break
    for n in names:
        for suffix in ("_threads", "_short"):
            p = root / ("caption_%s%s.txt" % (n, suffix))
            if p.exists():
                short = p
                break
        if short:
            break
    return full, short


def build_commands(item: dict) -> list[tuple[str, list[str]]]:
    """Список команд на одну единицу. Каждая — отдельный процесс: если упадёт
    Telegram, Instagram уже ушёл и не откатывается.

    В КАЖДОЙ команде стоит SEND_GUARD: без него дочерний скрипт делает пробный
    прогон и ничего не публикует. Убирать его отсюда нельзя — cmd_run() всё
    равно откажется запускать такую команду.
    """
    py = python_exe()
    target = item["target"]
    is_video = item["kind"] == "reel"
    plats = [str(p).strip() for p in item["platforms"]]
    full, short = caption_files(target)
    cmds: list[tuple[str, list[str]]] = []

    up = [p for p in plats if p in ("instagram", "telegram", "youtube", "threads")]
    ig_like = [p for p in up if p != "telegram"]
    if ig_like:
        c = [py, "autopost.py",
             "--video" if is_video else "--dir", target,
             "--to", ",".join(ig_like), SEND_GUARD]
        if full:
            c += ["--caption-file", str(full)]
        if is_video and item.get("trial") and "instagram" in ig_like:
            c += ["--trial", item["trial"]]
        cmds.append(("upload-post " + ",".join(ig_like), c))

    if "telegram" in up:
        # Отдельным вызовом и короткой подписью — иначе 400 валит весь заход.
        c = [py, "autopost.py",
             "--video" if is_video else "--dir", target,
             "--to", "telegram", SEND_GUARD]
        cap = short or full
        if cap:
            c += ["--caption-file", str(cap)]
        cmds.append(("telegram", c))

    if "pinterest" in plats:
        # Карусель тоже уходит — autopost.py сам ужимает её до обложки (их
        # приложение не показывает подпись у карусельных пинов, замерено
        # 22.08): полный текст остаётся в описании, слайды 2-8 не едут.
        c = [py, "autopost.py",
             "--video" if is_video else "--dir", target,
             "--to", "pinterest", SEND_GUARD]
        if full:
            c += ["--caption-file", str(full)]
        cmds.append(("pinterest", c))

    if "tiktok" in plats:
        # Отдельным вызовом, как Telegram: площадка подключена 22.08.2026 и
        # живьём не гонялась — если TikTok откажет, IG/YT/Threads/Telegram/ВК
        # этой единицы это не заденет.
        c = [py, "autopost.py",
             "--video" if is_video else "--dir", target,
             "--to", "tiktok", SEND_GUARD]
        if full:
            c += ["--caption-file", str(full)]
        cmds.append(("tiktok", c))

    if "vk" in plats:
        script = "vkpost.py" if is_video else "vkcarousel.py"
        c = [py, script,
             "--video" if is_video else "--dir", target, SEND_GUARD]
        if full:
            c += ["--caption-file", str(full)]
        cmds.append(("vk", c))

    return cmds


def cmd_add(a) -> int:
    q = load()
    zone_name = timezone_name()
    zone = tz_info(zone_name)
    try:
        naive = dt.datetime.strptime(a.at, "%Y-%m-%d %H:%M")
    except ValueError:
        print("Время выхода задаётся так: --at \"2026-08-15 09:15\" "
              "(год-месяц-день часы:минуты).")
        return 2
    when = naive.replace(tzinfo=zone)
    target = a.add
    kind = "reel" if str(target).lower().endswith((".mp4", ".mov")) else "carousel"
    iid = item_id(target)
    if any(i.get("id") == iid and i.get("status") in (STATUS_NEW, STATUS_OK)
           for i in q["items"]):
        print("В очереди уже есть незакрытая запись %s" % iid)
        return 1
    q["items"].append({
        "id": iid,
        "target": target,
        "kind": kind,
        "at": when.strftime("%Y-%m-%d %H:%M"),
        # Зону пишем В ЗАПИСЬ. Иначе смена настройки (или переезд ноутбука в
        # другой пояс) сдвинула бы уже утверждённый план — а он утверждён в тех
        # часах, в которых человек его читал. Имени зоны нет — кладём смещение,
        # посчитанное НА ЭТУ дату, чтобы не потерять переход на летнее время.
        "tz": zone_name or offset_str(naive, zone),
        "platforms": [p.strip() for p in a.to.split(",") if p.strip()],
        # ВСЕГДА pending. Ветки «а если без --hold — сразу approved» здесь больше
        # нет и быть не должно: это ровно та строка, из-за которой запись
        # выходила в ленту, не будучи никем одобренной.
        # pending → approved (только рукой человека) → done / failed / missed,
        # а без одобрения по истечении окна догона → expired.
        "status": STATUS_NEW,
        "added": now_local().strftime("%Y-%m-%d %H:%M"),
        **({"trial": a.trial} if a.trial else {}),
    })
    q["items"].sort(key=lambda i: i["at"])
    save(q)
    print("Добавлено: %s на %s (%s) → %s" % (iid, a.at, tz_label(zone_name), a.to))
    print("Само по себе это НЕ выйдет: запись ждёт вашего одобрения. "
          "Одобрить: --approve %s" % iid)
    return 0


def cmd_list(a) -> int:
    q = load()
    if not q["items"]:
        print("Очередь пуста. Часовой пояс выпуска: %s" % tz_label(timezone_name()))
        return 0
    print("Часовой пояс выпуска: %s" % tz_label(timezone_name()))
    waiting = []
    for i in q["items"]:
        status = i.get("status")
        # Запись со словом approved, но без отметки человека — это НЕ одобрено,
        # и в списке она обязана выглядеть как ожидание, а не как «готово».
        label = (STATUS_LABEL["approved"] if is_approved(i)
                 else STATUS_LABEL["pending"] if status == STATUS_OK
                 else STATUS_LABEL.get(status, status))
        if label == STATUS_LABEL["pending"]:
            waiting.append(i.get("id", "?"))
        print("%-16s %s %-7s %-34s %-22s %s" % (
            i.get("id", "?"), i.get("at", "?"), i.get("tz", ""), label,
            ",".join(i.get("platforms", [])), i.get("target", "")))
    if waiting:
        print("")
        print("Ждут вашего одобрения (%d): %s" % (len(waiting), ", ".join(waiting)))
        print("Пока не одобрите — не выйдет ничего. Одобрить: --approve %s"
              % ",".join(waiting))
    return 0


def cmd_approve(a) -> int:
    """Единственное место во всём заводе, где появляется слово «одобрено».

    Ставит и статус, и отметку времени. Отметка — не украшение: по ней --run
    отличает решение человека от статуса, который когда-то проставила машина.
    """
    q = load()
    ids = [x.strip() for x in a.approve.split(",")]
    stamp = now_local().strftime("%Y-%m-%d %H:%M")
    hit, revived, unknown = 0, [], [x for x in ids if x]
    for i in q["items"]:
        iid = i.get("id")
        if iid not in ids:
            continue
        if iid in unknown:
            unknown.remove(iid)
        # Одобряем и то, что ждёт, и то, что успело выйти из окна догона
        # (expired): человек вправе сказать «да» задним числом. Выйдет оно
        # только если по времени ещё попадает в окно, иначе честно станет
        # missed — но решение всё равно останется за человеком.
        if i.get("status") in (STATUS_NEW, "expired"):
            if i.get("status") == "expired":
                revived.append(iid)
            i["status"] = STATUS_OK
            i[APPROVED_MARK] = stamp
            i.pop("waiting_since", None)
            hit += 1
        elif i.get("status") == STATUS_OK and not is_approved(i):
            # Запись из старой версии: статус есть, решения человека не было.
            i[APPROVED_MARK] = stamp
            hit += 1
    save(q)
    print("Одобрено записей: %d" % hit)
    for iid in revived:
        print("%s: время выхода уже прошло — если оно вне окна догона, запись "
              "будет помечена «не вышло». Нужен новый выход — добавьте заново "
              "с новым --at." % iid)
    for iid in unknown:
        print("Записи %s в очереди нет — одобрять нечего." % iid)
    return 0


def cmd_unapprove(a) -> int:
    """Снять одобрение: запись возвращается в ожидание и сама не выйдет."""
    q = load()
    ids = [x.strip() for x in a.unapprove.split(",")]
    hit = 0
    for i in q["items"]:
        if i.get("id") in ids and i.get("status") == STATUS_OK:
            i["status"] = STATUS_NEW
            i.pop(APPROVED_MARK, None)
            hit += 1
    save(q)
    print("Одобрение снято: %d" % hit)
    if hit:
        print("Эти записи снова ждут вашего одобрения и по расписанию не выйдут.")
    return 0


def cmd_remove(a) -> int:
    q = load()
    before = len(q["items"])
    q["items"] = [i for i in q["items"] if i.get("id") != a.remove]
    save(q)
    print("Удалено записей: %d" % (before - len(q["items"])))
    return 0


def cmd_run(a) -> int:
    """Круг Планировщика: посмотреть очередь и выпустить ОДОБРЕННОЕ подошедшее.

    Порядок проверок здесь значим и написан так специально: сначала выясняем,
    одобрена ли запись, и только потом вообще думаем про публикацию. Ни одна
    ветка ниже не приводит к запуску команды для записи без is_approved().
    """
    q = load()
    root = install_root()
    changed = False

    for item in q["items"]:
        status = item.get("status")
        if status in STATUS_CLOSED:
            continue
        # Зона записи, а не текущая настройка: см. комментарий в cmd_add().
        # Старые записи (до появления поля) сделаны на заводской машине по
        # местному времени — там оно и есть МСК, поэтому запасной вариант
        # «текущая зона» их не сдвигает.
        zone = tz_info(item.get("tz") or timezone_name())
        try:
            when = dt.datetime.strptime(item["at"], "%Y-%m-%d %H:%M").replace(tzinfo=zone)
        except (KeyError, ValueError):
            item["status"] = "failed"
            changed = True
            log("НЕ ПОНЯТО ВРЕМЯ ВЫХОДА %s: %r" % (item.get("id"), item.get("at")))
            continue
        now = dt.datetime.now(zone)
        if when > now:
            # Время ещё не пришло. Молчим и про одобренные, и про ждущие:
            # круг Планировщика идёт раз в 5 минут, ему не место в журнале.
            continue
        late_h = (now - when).total_seconds() / 3600

        # ── РАЗВИЛКА ОДОБРЕНИЯ. Время подошло — и здесь решается всё.
        if not is_approved(item):
            if late_h > a.catchup_hours:
                # Окно догона кончилось, а «да» так и не прозвучало. Запись
                # получает СВОЙ статус: не missed (то про одобренное, которое
                # не смогли выпустить), а expired — про несостоявшееся согласие.
                print("%s: время выхода прошло (%.1f ч назад), одобрения не "
                      "было — запись закрыта и сама не выйдет. Нужен выход — "
                      "добавьте заново с новым временем." % (item["id"], late_h))
                if not a.dry_run:
                    item["status"] = "expired"
                    changed = True
                    log("НЕ ОДОБРЕНО, ВРЕМЯ ВЫШЛО %s (опоздание %.1f ч)"
                        % (item["id"], late_h))
                continue
            # Окно ещё открыто: запись жива и ждёт человека. Если старая версия
            # успела написать ей «approved» без спроса — снимаем это слово, оно
            # никем не сказано.
            print("%s: время выхода подошло, но одобрения нет — жду. "
                  "Одобрить: --approve %s" % (item["id"], item["id"]))
            if not a.dry_run:
                if status == STATUS_OK:
                    item["status"] = STATUS_NEW
                    changed = True
                    log("СНЯТО САМОЗВАНОЕ «одобрено» %s: решения человека в "
                        "записи нет" % item["id"])
                # В журнал — ОДИН раз на запись, а не каждые 5 минут: круг
                # Планировщика частый, и журнал забился бы одной и той же
                # строкой так, что настоящие ошибки в нём стало бы не найти.
                if not item.get("waiting_since"):
                    item["waiting_since"] = now.strftime("%Y-%m-%d %H:%M")
                    changed = True
                    log("ЖДУ ОДОБРЕНИЯ %s (время выхода подошло)" % item["id"])
            continue

        # Дальше — только одобренное человеком.
        if late_h > a.catchup_hours:
            item["status"] = "missed"
            changed = True
            log("ПРОПУЩЕНО %s (опоздание %.1f ч)" % (item["id"], late_h))
            continue

        ok = True
        for label, cmd in build_commands(item):
            # Предохранитель. Дошли сюда без --send — это ошибка кода, а не
            # клиента: лучше не опубликовать, чем опубликовать непрошеное.
            if SEND_GUARD not in cmd:
                log("ОТКАЗ %s %s: в команде нет %s" % (item["id"], label, SEND_GUARD))
                ok = False
                continue
            script = root / cmd[1]
            if not script.is_file():
                # Файла нет в установке — говорим об этом по-русски, а не падаем
                # FileNotFoundError внутри задания Планировщика, где вывод никто
                # не увидит.
                msg = ("нет файла %s в папке завода — публикация в «%s» не ушла"
                       % (cmd[1], label))
                print(msg)
                log("ОШИБКА %s: %s" % (item["id"], msg))
                ok = False
                continue
            if a.dry_run:
                print("[dry-run] %s: %s" % (label, " ".join(cmd)))
                continue
            r = subprocess.run(cmd, cwd=str(root))
            log("%s %s -> код %d" % (item["id"], label, r.returncode))
            if r.returncode != 0:
                ok = False
        if a.dry_run:
            continue
        item["status"] = "done" if ok else "failed"
        item["published_at"] = now.strftime("%Y-%m-%d %H:%M")
        changed = True

    if changed:
        save(q)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Очередь публикаций по расписанию")
    ap.add_argument("--add", help="папка карусели или файл ролика")
    ap.add_argument("--at", help="время выхода: \"2026-08-15 09:15\" "
                                 "(в часовом поясе выпуска, см. --tz)")
    ap.add_argument("--to", default="instagram,telegram,vk,tiktok,pinterest",
                    help="площадки через запятую")
    ap.add_argument("--tz", help="часовой пояс выпуска: имя зоны (Asia/Yekaterinburg) "
                                 "или смещение (+05:00). По умолчанию — из настроек "
                                 "клиента, а если их нет — часовой пояс компьютера")
    ap.add_argument("--trial", choices=["auto", "manual"],
                    help="Reels в Instagram: пробный показ (см. autopost.py "
                         "--trial) — прокидывается только на instagram")
    # --hold остаётся ради старых вызовов из скриптов и документов, но ничего
    # больше не значит: придержаны теперь ВСЕ записи без исключения. Убирать
    # флаг нельзя — старый вызов упал бы «unrecognized arguments» прямо внутри
    # задания Планировщика, где вывод никто не увидит.
    ap.add_argument("--hold", action="store_true",
                    help="ничего не меняет: запись и так ложится ждущей "
                         "одобрения (флаг оставлен для старых вызовов)")
    ap.add_argument("--list", action="store_true", help="показать очередь")
    ap.add_argument("--approve", help="ОДОБРИТЬ к выходу: id через запятую. "
                                      "Без этого не выйдет ничего")
    ap.add_argument("--unapprove", help="снять одобрение: id через запятую — "
                                        "запись вернётся в ожидание")
    ap.add_argument("--remove", help="убрать запись из очереди")
    ap.add_argument("--run", action="store_true", help="опубликовать подошедшее")
    ap.add_argument("--dry-run", action="store_true", help="показать, не отправляя")
    ap.add_argument("--catchup-hours", type=float, default=6.0,
                    help="насколько поздно ещё можно догнать пропущенное")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass

    _TZ_OVERRIDE.clear()
    if a.tz:
        _TZ_OVERRIDE.append(a.tz.strip())

    if a.add:
        if not a.at:
            print("--add требует --at \"ГГГГ-ММ-ДД ЧЧ:ММ\"")
            return 2
        return cmd_add(a)
    if a.approve:
        return cmd_approve(a)
    if a.unapprove:
        return cmd_unapprove(a)
    if a.remove:
        return cmd_remove(a)
    if a.run:
        return cmd_run(a)
    return cmd_list(a)


if __name__ == "__main__":
    sys.exit(main())
