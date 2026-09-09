# -*- coding: utf-8 -*-
"""
factory_config — канонический рантайм-ридер конфига «Контент-завода».

Единственный контракт чтения настроек для ВСЕХ модулей коробки. Модуль больше не
пишет свою функцию load_env / чтения config.json — он импортирует этот модуль:

    from factory_config import load, get_secret, gdrive_root, root

    cfg   = load("publishing")          # несекретные ответы клиента (dict)
    token = get_secret("vk_token")      # секрет из окружения / .env (str | None)
    drive = gdrive_root()               # Path к <GoogleDrive>/КонтентЗавод

ГДЕ ЧТО ЛЕЖИТ (относительно корня установки, без абсолютных путей):
    <root>/.factory_root                 — маркер корня (создаёт установщик)
    <root>/config/factory.config.json    — НЕсекретные ответы (в git не идёт у клиента)
    <root>/.env                          — секреты, права 0600, gitignored

ПРАВИЛА КОНТРАКТА (CONTRACT.md §3):
    - никаких абсолютных путей: корень ищется вверх по дереву по маркеру;
    - секреты только вне git и НИКОГДА не логируются этим модулем;
    - всё, чего нет, возвращается как None/{} — модуль работает в урезанном режиме,
      а не падает.

Кроссплатформенно (Windows — целевая ОС клиента). Только stdlib.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# Маркеры корня установки. Первый — канонический (создаётся установщиком и
# ПОПАДАЕТ в коробку). CONTRACT.md и modules/ — запасные для дев-окружения.
_ROOT_MARKERS = (".factory_root", "config/factory.config.json", "modules", "CONTRACT.md")

_ENV_LOADED = False
_CONFIG_CACHE: Optional[dict] = None


def _canon(key: str) -> str:
    """Канонизация ключа вопроса: lower_snake_case, без учёта регистра.

    upload_post_api_key и UPLOAD_POST_API_KEY — один и тот же ключ.
    """
    return key.strip().lower()


def root(start: Optional[Path] = None) -> Path:
    """Корень установки.

    Порядок разрешения (без абсолютных путей в коде):
      1. переменная окружения FACTORY_ROOT, если задана и существует;
      2. ближайший канонический маркер .factory_root — среди цепочек от start,
         cwd и этого файла берём САМЫЙ ГЛУБОКИЙ (на случай вложенных деревьев);
      3. запасные маркеры (config/, modules/, CONTRACT.md) — первый найденный;
      4. install/ лежит внутри корня → корень на уровень выше; иначе cwd.
    """
    env_root = os.environ.get("FACTORY_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root).resolve()

    bases = []
    if start is not None:
        bases.append(Path(start).resolve())
    bases.append(Path.cwd())
    bases.append(Path(__file__).resolve().parent)

    # Пасс 1: канонический .factory_root, самый глубокий из найденных.
    canonical = []
    for base in bases:
        for cand in (base, *base.parents):
            if (cand / ".factory_root").exists():
                canonical.append(cand)
    if canonical:
        return max(canonical, key=lambda p: len(p.parts))

    # Пасс 2: запасные маркеры.
    for base in bases:
        for cand in (base, *base.parents):
            for marker in _ROOT_MARKERS[1:]:
                if (cand / marker).exists():
                    return cand

    here = Path(__file__).resolve().parent
    return here.parent if here.name == "install" else Path.cwd()


def config_path() -> Path:
    return root() / "config" / "factory.config.json"


def env_path() -> Path:
    return root() / ".env"


def _parse_env(path: Path) -> dict:
    out: dict = {}
    if not path.exists():
        return out
    # utf-8-sig: у клиента на Windows файл может прийти с BOM.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _env_values() -> dict:
    """Значения из `.env` БЕЗ побочных действий: ничего не пишем в окружение.

    Кэша нет намеренно: корень установки подменяется на лету (FACTORY_ROOT — так
    делают тесты и мастер установки), а закэшированный `.env` от прежнего корня
    отдавал бы чужие значения. Файл маленький, читать его каждый раз дешевле,
    чем городить инвалидацию.
    """
    return _parse_env(env_path())


def _load_env_into_environ() -> dict:
    """Подмешать .env в os.environ (не перетирая уже заданное). ПО ЯВНОМУ ЗОВУ.

    TAIL-11 (28.08.2026). Раньше это звалось САМО — из `load()` и из
    `get_secret()`. То есть первое же чтение конфига ЛЮБЫМ модулем оставляло
    все девять ключей соцсетей в окружении процесса до самого выхода, и они
    наследовались каждым подпроцессом, который завод запускает.

    Чем это плохо на деле:
      * предохранитель тестов чистит окружение ОДИН РАЗ на старте процесса, а
        заливка происходила ПОСЛЕ него — вычистка отменялась. Спасала только
        блокировка сокетов; сняли бы её — и ключи снова доступны любому тесту
        (вскрылось четырьмя падениями при написании тестов SmmBox);
      * ключ соцсети попадал в окружение процессов, которым он не нужен вовсе —
        ffmpeg, распознавание речи, любой subprocess.

    Мост совместимости при этом НЕ нужен: легаси-читатели (`tg_send.py`,
    `notify_commits.py`, `tg_report.py`, `modules/analytics/upload_post.py`)
    разбирают `.env` сами и смотрят в `os.environ` лишь как в приоритетный
    источник — сверено поимённо. Поэтому функция осталась, но зовётся только
    осознанно, а `load()`/`get_secret()` работают без побочных действий.

    Значения секретов здесь НЕ логируются.
    """
    global _ENV_LOADED
    data = _env_values()
    if not _ENV_LOADED:
        for k, v in data.items():
            os.environ.setdefault(k, v)
        _ENV_LOADED = True
    return data


def load(module_name: Optional[str] = None, *, refresh: bool = False) -> dict:
    """Несекретные ответы клиента из config/factory.config.json.

    Ключи глобальны (плоский namespace вопросов из манифестов), поэтому
    module_name сейчас носит справочный характер и возвращается общий конфиг.
    Если файла нет — {} (первый запуск / урезанный режим).
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None or refresh:
        p = config_path()
        if p.exists():
            try:
                _CONFIG_CACHE = json.loads(p.read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, OSError):
                _CONFIG_CACHE = {}
        else:
            _CONFIG_CACHE = {}
    # .env В ОКРУЖЕНИЕ БОЛЬШЕ НЕ ЗАЛИВАЕМ (TAIL-11). Здесь читаются НЕсекретные
    # ответы клиента, и подмешивать ради них девять ключей соцсетей в окружение
    # процесса — цена, которой никто не просил. Кому нужны секреты, тот зовёт
    # get_secret(); кому нужен именно легаси-мост — _load_env_into_environ()
    # явно.
    return dict(_CONFIG_CACHE)


def get(key: str, default=None):
    """Несекретное значение по ключу (без учёта регистра)."""
    cfg = load()
    ck = _canon(key)
    for k, v in cfg.items():
        if _canon(k) == ck:
            return v
    return default


def get_secret(key: str) -> Optional[str]:
    """Секрет по ключу вопроса. Ищем в окружении и .env без учёта регистра.

    Мост имён: ключ вопроса канонизируется в lower_snake_case, но модули и .env
    исторически держат секреты в UPPER_SNAKE (UPLOAD_POST_API_KEY). Поэтому
    пробуем ключ как есть, в UPPER и в lower. Возвращаем None, если нет
    (модуль уходит в урезанный режим, а не падает). Значение не логируется.
    """
    # Читаем .env, НЕ трогая окружение процесса (TAIL-11): ниже он и так
    # просматривается наравне с os.environ, заливка ничего не добавляла.
    env = _env_values()
    variants = {key, key.upper(), key.lower(), _canon(key).upper(), _canon(key)}
    for src in (os.environ, env):
        for name in variants:
            if name in src and src[name] != "":
                return src[name]
    # Последняя попытка — сопоставление без учёта регистра по всему окружению.
    target = _canon(key)
    for src in (os.environ, env):
        for k, v in src.items():
            if _canon(k) == target and v != "":
                return v
    return None


def has_secret(key: str) -> bool:
    return get_secret(key) is not None


def gdrive_root() -> Optional[Path]:
    """Path к <GoogleDrive>/КонтентЗавод или None, если клиент его не задал."""
    base = get("gdrive_root")
    if not base:
        return None
    p = Path(base).expanduser()
    return p / "КонтентЗавод" if p.name != "КонтентЗавод" else p


def answers_memory() -> dict:
    """Ответы клиента на вопросы мастера, запомненные между установками (BOX-17).

    Пишет их install/questions.py в config/answers.memory.json. Секретов там нет
    по построению. Значение '__later__' означает «клиент отложил вопрос» — модулю
    это то же самое, что «ответа нет», поэтому такие ключи сюда не отдаём.
    Нет файла — {} (первая установка).
    """
    p = root() / "config" / "answers.memory.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if v != "__later__"}


def answer(key: str, default=None):
    """Ответ клиента: сначала конфиг этапов, потом память мастера.

    Позволяет модулю прочитать выбор, которого ещё нет в factory.config.json
    (например пресет оформления, выбранный в мастере до появления модуля стиля).
    """
    v = get(key)
    if v not in (None, ""):
        return v
    ck = _canon(key)
    for k, val in answers_memory().items():
        if _canon(k) == ck and val not in (None, ""):
            return val
    return default


def style_file() -> Optional[Path]:
    """Файл выбранного клиентом пресета оформления из styles/ или None.

    Клиент выбирает пресет по ИМЕНИ (поле name внутри файла стиля), а не по пути,
    поэтому здесь имя разрешается в файл поиском по styles/*.json. Пресета нет
    или папки styles/ нет — None, и движок рисует своим оформлением по умолчанию.
    """
    name = answer("brand_preset")
    if not name:
        return None
    d = root() / "styles"
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and str(data.get("name", "")) == str(name):
            return p
    return None


def carousel_registry() -> dict:
    """Реестр шаблонов каруселей (styles/carousel_templates.json) или {}.

    Данные, а не код: новое семейство/акцент добавляется правкой json, без
    правки движков. Файла нет — {}, и вызывающий работает как «шаблонов не
    знаю» (контракт §3: отсутствие данных не роняет модуль).
    """
    p = root() / "styles" / "carousel_templates.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def carousel_template() -> Optional[dict]:
    """Фирменный шаблон каруселей клиента или None, если он его не выбирал.

    Хранится ключом `carousel_template` значением «семейство:акцент» (white:mint).
    Клиент мог назвать только цвет («хочу терракоту») — тогда значение из одного
    слова, и семейство берётся по умолчанию для этого цвета из реестра.

    None — это НЕ поломка, а честное «фирменного шаблона ещё нет»: ровно
    состояние свежей установки, когда завод показывает витрину и спрашивает
    глазами. Сюда же приводит сброс «верни исходный стиль каруселей».

    Возвращает {"family","accent","engine","title"} — всё, что нужно скрипту
    сборки, чтобы выбрать движок и цвет.
    """
    raw = str(answer("carousel_template") or "").strip().lower()
    if not raw:
        return None

    reg = carousel_registry()
    families = reg.get("families") or {}
    accents = [str(a) for a in (reg.get("accents") or [])]
    by_accent = reg.get("_default_family_by_accent") or {}

    if ":" in raw:
        family, _, accent = raw.partition(":")
    else:
        # назван один цвет — семейство по умолчанию для него
        accent = raw
        family = str(by_accent.get(accent, "") or "")
    family, accent = family.strip(), accent.strip()

    # Записан мусор (руками, не через setup_config) — трактуем как «не выбран»,
    # а не падаем: движок соберёт дефолтом, клиент увидит витрину заново.
    if family not in families or (accents and accent not in accents):
        return None

    f = families[family]
    return {"family": family, "accent": accent,
            "engine": str(f.get("engine", "")), "title": str(f.get("title", ""))}

