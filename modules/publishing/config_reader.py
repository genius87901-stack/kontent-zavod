# -*- coding: utf-8 -*-
"""ЕДИНЫЙ РИДЕР НАСТРОЕК ПУБЛИКАЦИИ — один и тот же для доктора и для боевого кода.

ЗАЧЕМ ЭТОТ ФАЙЛ ПОЯВИЛСЯ (аудит 22.08.2026, перед заведением ключей клиентами).
Мастер установки раскладывает ответы клиента ПО ДВУМ ФАЙЛАМ: помеченные в
манифесте `"secret": true` уходят в `.env`, остальные — в
`config/factory.config.json` (install/setup_config.py). А корневые скрипты
публикации читали ТОЛЬКО `.env`. Значит заведённый клиентом `UPLOAD_POST_USER`
(это имя профиля, не секрет — и помечен `"secret": false`) не доходил до
`autopost.creds()` вовсе, и первая же публикация падала словами
«НЕ ХВАТАЕТ В .env: UPLOAD_POST_USER» — при полностью заполненном мастере.
Тем же были задеты `VK_GROUP_ID`, `VK_APP_ID`, `TG_CHAT_ID`.

Хуже самой поломки было расхождение: `modules/publishing/doctor.py` читал ОБА
файла и на той же раскладке отвечал «готово». Клиент видел зелёный отчёт и
падающую публикацию. Поэтому лечим не подстановкой флага в манифест, а общим
ридером: теперь и доктор, и боевой код спрашивают настройки ЗДЕСЬ, и разойтись
им больше негде.

ПОЧЕМУ НЕ «пометить ключи secret:true». Номер сообщества и имя профиля секретами
не являются: установщик кладёт секреты отдельно и обращается с ними иначе. Врать
манифестом — значит чинить симптом у одного модуля и оставить грабли следующему,
у кого появится несекретный ключ. Плюс install/README.md («Контракт чтения
конфига») прямо предписывает читать оба источника через общий ридер — корневые
скрипты просто не следовали предписанию.

ЧТО ОТКУДА (порядок приоритета — сверху вниз):
    1. os.environ                       — ручной прогон, CI, временная подмена
    2. <корень>/.env                    — секреты (secret: true)
    3. <корень>/config/factory.config.json — несекретные ответы (secret: false)

Пустая строка = значения нет (клиент нажал Enter, не заполнив).

КОРЕНЬ УСТАНОВКИ ищет install/factory_config.py — канонический ридер слоя
установки. Он чужой (правим только читаем), поэтому подключаем его по пути, а не
импортом пакета, и на любой сбой откатываемся на собственный поиск маркеров:
отсутствие слоя установки не должно ронять публикацию.

Только stdlib. Кроссплатформенно (целевая ОС клиента — Windows).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

# Маркеры корня установки — те же, что у install/factory_config.py. Дублируются
# только на случай, когда самого factory_config рядом не оказалось.
_MARKERS = (".factory_root", "config/factory.config.json", "modules", "CONTRACT.md")

_FC_CACHE: list = []       # [] — ещё не грузили; [mod|None] — загружено


def _factory_config():
    """install/factory_config.py как модуль (он не пакет и не лежит на PYTHONPATH).

    Тот же приём, каким doctor.py подключает install/check_env.py. Любая ошибка
    загрузки — это None и работа по запасному пути, а не падение публикации.
    """
    if _FC_CACHE:
        return _FC_CACHE[0]
    mod = None
    try:
        import importlib.util
        for parent in Path(__file__).resolve().parents:
            cand = parent / "install" / "factory_config.py"
            if cand.is_file():
                spec = importlib.util.spec_from_file_location(
                    "factory_config_for_publishing", cand)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                break
    except Exception:
        mod = None
    _FC_CACHE.append(mod)
    return mod


def root(start: Optional[object] = None) -> Path:
    """Корень установки: там лежат и `.env`, и `config/factory.config.json`.

    FACTORY_ROOT (если задан и существует) — сильнее всего: им пользуются и
    установщик, и тесты. Дальше спрашиваем install/factory_config.root(), и лишь
    если его нет — ищем маркеры сами, вверх от `start` (обычно это каталог
    вызвавшего скрипта).
    """
    env_root = os.environ.get("FACTORY_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root).resolve()

    fc = _factory_config()
    if fc is not None:
        try:
            return Path(fc.root(Path(start) if start else None)).resolve()
        except Exception:
            pass

    base = Path(start).resolve() if start else Path(__file__).resolve().parent
    for cand in (base, *base.parents):
        for marker in _MARKERS:
            if (cand / marker).exists():
                return cand
    return Path.cwd()


def config_path(start: Optional[object] = None) -> Path:
    return root(start) / "config" / "factory.config.json"


def env_path(start: Optional[object] = None) -> Path:
    return root(start) / ".env"


def _flat(data: dict) -> dict:
    """Простые значения ответов, приведённые к строке; пустые — выбрасываем.

    Числа приводим сами: мастер спрашивает номер сообщества словами «только
    цифры», и значение вполне может лечь в json числом. Прежний doctor такие
    ключи молча ронял (`isinstance(v, str)`) и объявлял заполненное — пустым.
    """
    out: dict = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (str, int, float)):
            s = str(v).strip()
            if s:
                out[str(k).strip()] = s
    return out


def parse_env_file(path: Path) -> dict:
    """Разбор `.env` — те же правила, что у всех скриптов завода.

    utf-8-sig: у клиента на Windows файл нередко приходит с BOM, и без этого
    первый ключ в файле читается как «﻿UPLOAD_POST_API_KEY».
    """
    out: dict = {}
    try:
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                out[k.strip()] = v
    except OSError:
        pass
    return out


# ── ОДНО ИМЯ НА ОДИН КЛЮЧ (аудит 22.08.2026, «дыра трёх имён») ──────────────
# Один и тот же пользовательский токен ВК звался в проекте по-разному:
#   * `VK_USER_TOKEN_KATE` — так его читают vkcarousel.py, vkpost.py и драйвер
#     сторис; на нём прошли ЖИВЫЕ публикации (карусель 29.07, клип r37 29.07);
#   * `vk_stories_token`   — так о нём спрашивал мастер установки у модуля
#     story-publisher.
# Клиент отвечал мастеру ОДИН раз, ключ ложился под ОДНИМ именем — и половина
# завода его не находила: доктор сторис говорил «готово», а карусель падала
# словами «В .env нет VK_USER_TOKEN_KATE». Чинить это в каждом файле отдельно
# значит завести четвёртое имя. Поэтому канон объявляется ЗДЕСЬ, а все читатели
# спрашивают ключ через этот ридер.
#
# КАНОН — `VK_USER_TOKEN_KATE`. Выбран не по красоте: это имя стоит в коде,
# который ДЕЙСТВИТЕЛЬНО публиковал (см. docs/PUBLISH_READINESS.md, «БОЙ»).
# Переименовать его означало бы тронуть единственный проверенный боем путь ВК
# ради косметики. Остальные имена остаются рабочими как алиасы — уже
# установленные копии клиентов, ответивших мастеру раньше, не ломаются.
#
# ⚠️ НЕ АЛИАС: `VK_USER_TOKEN` (формат vk2.a., VK ID). Это ДРУГОЙ токен, а не
# другое имя того же: он живёт час, не умеет ни `wall.post`, ни фото на стену
# (ошибки 1051/15) и годится только для видео. Смешать их именем — значит
# отправить карусель в заведомо закрытый метод.
ALIASES = {
    "VK_USER_TOKEN_KATE": ("VK_STORIES_TOKEN",),
}

_ALIAS_OF = {}
for _canon_name, _alts in ALIASES.items():
    _ALIAS_OF[_canon_name] = _canon_name
    for _alt in _alts:
        _ALIAS_OF[_alt] = _canon_name


def canon(key: str) -> str:
    """Каноническое имя ключа. Незнакомые ключи возвращаются как есть."""
    k = str(key).strip()
    return _ALIAS_OF.get(k.upper(), k)


def names_for(key: str) -> tuple:
    """Все имена, под которыми ключ мог быть заведён. Канон — первым."""
    c = canon(key)
    return (c,) + tuple(ALIASES.get(c, ()))


def _spread_aliases(data: dict) -> dict:
    """Заведённый под одним именем ключ становится виден под ВСЕМИ своими.

    Нужно потому, что боевой код спрашивает словарь напрямую
    (`env.get("VK_USER_TOKEN_KATE")` в vkcarousel.py), а ответ клиента мог лечь
    под именем алиаса. Регистр не важен: `.env` у клиента на Windows приходит
    каким угодно.
    """
    upper = {}
    for k, v in data.items():
        if str(v).strip():
            upper.setdefault(str(k).strip().upper(), str(v).strip())
    for c, alts in ALIASES.items():
        names = (c,) + tuple(alts)
        val = next((upper[n] for n in names if upper.get(n)), None)
        if val is None:
            continue
        for n in names:
            if not str(data.get(n, "")).strip():
                data[n] = val
    return data


def _config_dict(r: Path) -> dict:
    """Несекретные ответы клиента. Сначала — через install/factory_config.

    Читаем именно им (install/README.md, «Контракт чтения конфига»): это его
    зона ответственности, включая кодировку и кеш. `refresh=True` обязателен —
    у него конфиг кешируется на процесс, а нам нужен текущий корень, а не тот,
    что попался первым. FACTORY_ROOT подставляем на время вызова, чтобы он
    смотрел ровно в наш корень. Не получилось — читаем файл сами, теми же
    правилами: слой установки может отсутствовать (обрезанная копия), а
    публикация обязана работать.
    """
    fc = _factory_config()
    if fc is not None:
        old = os.environ.get("FACTORY_ROOT")
        os.environ["FACTORY_ROOT"] = str(r)
        try:
            return _flat(fc.load(refresh=True))
        except Exception:
            pass
        finally:
            if old is None:
                os.environ.pop("FACTORY_ROOT", None)
            else:
                os.environ["FACTORY_ROOT"] = old
    try:
        p = r / "config" / "factory.config.json"
        return _flat(json.loads(p.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return {}


def merged(start: Optional[object] = None) -> dict:
    """Все настройки одним словарём: config/factory.config.json + .env.

    `.env` кладётся ПОВЕРХ конфига: если ключ почему-то оказался в обоих файлах,
    выигрывает тот, что установщик считает секретом. os.environ сюда НЕ вливаем
    целиком (там тысячи посторонних переменных) — его смотрит `value()`.

    Возвращает копию: вызывающий волен дописывать в неё своё (так делает
    vkpost.save_env, дописывая обновлённый токен).
    """
    r = root(start)
    out = _config_dict(r)
    out.update(parse_env_file(r / ".env"))
    return _spread_aliases(out)


def value(key: str, start: Optional[object] = None,
          env: Optional[dict] = None) -> Optional[str]:
    """Одно значение по ключу: os.environ > .env > factory.config.json.

    `env` — уже посчитанный `merged()`, чтобы не перечитывать файлы в цикле.
    Пустая строка — это «нет значения», а не значение: клиент мог пройти вопрос
    мастера, ничего не введя.

    Ключ ищется под ВСЕМИ своими именами (см. ALIASES) и БЕЗ УЧЁТА РЕГИСТРА —
    то же правило, что у install/factory_config.py::_canon. Иначе ответ клиента,
    легший под именем алиаса или в другом регистре, для доктора не существует, и
    доктор объявляет ненастроенной полностью настроенную коробку.
    """
    names = names_for(key)
    for name in names:
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip()
    src = merged(start) if env is None else env
    for name in names:
        v = src.get(name)
        if v and str(v).strip():
            return str(v).strip()
    want = {n.upper() for n in names}
    for k, v in src.items():
        if str(k).strip().upper() in want and v and str(v).strip():
            return str(v).strip()
    return None


def values(keys: Iterable[str], start: Optional[object] = None) -> dict:
    """{ключ: значение или None} за одно чтение файлов."""
    env = merged(start)
    return {k: value(k, env=env) for k in keys}


def missing(keys: Iterable[str], start: Optional[object] = None) -> list:
    """Каких ключей не хватает — в порядке, в котором их перечислили."""
    got = values(keys, start)
    return [k for k in keys if not got.get(k)]
