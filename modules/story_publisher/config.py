# -*- coding: utf-8 -*-
"""Чтение конфига модуля story-publisher — ЕДИНСТВЕННАЯ точка доступа.

Контракт (CONTRACT.md §3, §4 + бриф OWNER):
  * Корень установки = каталог с CONTRACT.md. Все пути — через pathlib
    относительно него. Никаких абсолютных путей в коде.
  * Несекретный конфиг: config/factory.config.json (ключи = key из
    config_questions манифеста).
  * Секреты (secret: true в манифесте) — ТОЛЬКО из переменных окружения.
    Значения секретов не логируются и не пишутся в файлы.
  * Мультитенантность: client_id выбирает секцию конфига конкретного клиента.
    Без client_id работает «клиент по умолчанию» (плоский верхний уровень —
    текущий одноклиентный режим).

Когда OWNER даст общий ридер конфига — подменить этот файл целиком, снаружи
никто не лезет в JSON/окружение напрямую, только через config_for().
"""
from __future__ import annotations

import json
import os
from pathlib import Path


# --- размещение корня установки -------------------------------------------

def install_root():
    """Каталог, в котором лежит CONTRACT.md. Это корень установки.

    Идём вверх от этого файла и ищем CONTRACT.md. Если не нашли (например,
    модуль вынут из архива) — берём родителя пакета: modules/story_publisher/
    → на два уровня выше корень.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CONTRACT.md").exists():
            return parent
    # запасной вариант: .../modules/story_publisher/config.py -> parents[2]
    return here.parents[2]


def _config_path():
    return install_root() / "config" / "factory.config.json"


# --- секреты из .env завода ------------------------------------------------
# Модуль читает секреты из окружения. На машине завода ключи живут в .env корня
# (TG_BOT_TOKEN, UPLOAD_POST_API_KEY, VK_USER_TOKEN_KATE…), как у autopost.py и
# tg_send.py. Подгружаем их в окружение ОДИН раз, НЕ перетирая уже заданное
# (установщик клиента задаёт env сам — его значения приоритетнее .env-файла).
_ENV_LOADED = False

# --- канон имён ключей: спрашиваем ОБЩИЙ ридер, своего не заводим ------------
# ЗАЧЕМ. Один и тот же токен ВК звался `vk_stories_token` в вопросе мастера у
# этого модуля и `VK_USER_TOKEN_KATE` в корневых скриптах. Клиент отвечал один
# раз — и половина завода ключ не находила. Таблица имён теперь ОДНА, живёт в
# modules/publishing/config_reader.py (ALIASES) и обслуживает всех читателей.
# Грузим её ПО ПУТИ: publishing — не пакет на PYTHONPATH, а этот модуль
# запускают и как `python -m story_publisher.doctor`. Не нашли (обрезанная
# копия без publishing) — работаем без алиасов: сторис не должны переставать
# собираться из-за отсутствия соседнего модуля.
_CR_CACHE: list = []


def _config_reader():
    if _CR_CACHE:
        return _CR_CACHE[0]
    mod = None
    try:
        import importlib.util
        for parent in Path(__file__).resolve().parents:
            cand = parent / "publishing" / "config_reader.py"
            if cand.is_file():
                spec = importlib.util.spec_from_file_location(
                    "publishing_config_reader_for_stories", cand)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                break
    except Exception:
        mod = None
    _CR_CACHE.append(mod)
    return mod


def alias_names(key):
    """Все имена, под которыми ключ мог быть заведён. Само имя — первым."""
    cr = _config_reader()
    if cr is not None:
        try:
            names = tuple(cr.names_for(key))
        except Exception:
            names = ()
        if names:
            # Спрошенное имя оставляем первым: вызывающий назвал его не случайно.
            rest = [n for n in names if n.lower() != str(key).lower()]
            return (str(key),) + tuple(rest)
    return (str(key),)


def _ensure_env_loaded():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = install_root() / ".env"
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _load_config_file():
    """Прочитать несекретный JSON-конфиг. Нет файла — пустой конфиг (урезанный
    режим, а не падение: см. CONTRACT §1 про required: false)."""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        # Битый или недоступный конфиг не должен ронять модуль стектрейсом —
        # doctor человеческим языком объяснит, что не так.
        return {}
    return data if isinstance(data, dict) else {}


# --- единая точка чтения значений -----------------------------------------

def _env_lookup(key, client_id):
    """Секрет из окружения. Пробуем, в порядке приоритета:
      1) KEY__CLIENTID  (мультитенант, на будущее)
      2) KEY            (общий/одноклиентный — текущий режим)
    Ключ в окружении — В ВЕРХНЕМ РЕГИСТРЕ (так же как в .env: UPLOAD_POST_API_KEY).
    Дублируем попытку в исходном регистре ключа на случай иного соглашения
    установщика.
    """
    names = []
    for alias in alias_names(key):
        upper = alias.upper()
        if client_id:
            names.append("%s__%s" % (upper, str(client_id).upper()))
            names.append("%s__%s" % (alias, client_id))
        names.append(upper)
        names.append(alias)
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


def config_value(key, client_id=None, secret=False, default=None):
    """ЕДИНСТВЕННАЯ функция чтения конфига. Всё остальное зовёт только её.

    secret=True  → значение берётся из переменных окружения (не из JSON и
                   никогда не логируется).
    secret=False → значение берётся из config/factory.config.json; при заданном
                   client_id секция clients[client_id] переопределяет верхний
                   уровень.
    """
    if secret:
        _ensure_env_loaded()
        val = _env_lookup(key, client_id)
        return val if val is not None else default

    data = _load_config_file()
    # Ключ ищем под всеми его именами (alias_names) — ровно так же, как секрет
    # выше и как общий ридер. Иначе доктор и драйвер разойдутся именно в тех
    # случаях, ради которых алиасы и заведены.
    for alias in alias_names(key):
        if client_id:
            section = data.get("clients")
            if isinstance(section, dict):
                client_cfg = section.get(str(client_id))
                if isinstance(client_cfg, dict):
                    hit = _lookup_ci(client_cfg, alias)
                    if hit is not None:
                        return hit
        hit = _lookup_ci(data, alias)
        if hit is not None:
            return hit
    return default


def _lookup_ci(data, key):
    """Значение по ключу БЕЗ УЧЁТА РЕГИСТРА. None — ключа нет.

    ЗАЧЕМ. Мастер установки пишет ответы в config/factory.config.json под теми
    именами, что стоят в манифестах, а они разнорегистровые: publishing спрашивает
    `TG_CHAT_ID`, наш модуль читает `tg_chat_id`. При точном сравнении клиент,
    честно ответивший мастеру про канал Telegram, получал от нас «канал не задан»
    — то есть «сломано» на работающей настройке. Канонизация ключа — то же
    правило, что в общем ридере install/factory_config.py::_canon.
    """
    if not isinstance(data, dict):
        return None
    if key in data:
        return data[key]
    target = str(key).strip().lower()
    for k, v in data.items():
        if str(k).strip().lower() == target:
            return v
    return None


class ClientConfig:
    """Тонкая обёртка над config_value, привязанная к одному client_id.

    Удобна драйверам: cfg.secret("upload_post_api_key") / cfg.value("upload_post_user").
    Никакого своего доступа к файлам/окружению — только через config_value.
    """

    def __init__(self, client_id=None):
        self.client_id = client_id

    def value(self, key, default=None):
        return config_value(key, client_id=self.client_id, secret=False, default=default)

    def secret(self, key, default=None):
        return config_value(key, client_id=self.client_id, secret=True, default=default)

    def has_secret(self, key):
        """Есть ли секрет — БЕЗ раскрытия значения (для doctor/логов)."""
        return bool(self.secret(key))

    def any_value(self, *keys, default=None):
        """Первое непустое значение по любому из ключей, из ЛЮБОГО источника.

        Нужна там, где ответ клиента может лежать и в окружении, и в несекретном
        конфиге, а сам ключ исторически зовётся по-разному (`tg_story_chat_id`
        своего модуля и общий `tg_chat_id` из publishing). Доктор и драйвер обязаны
        смотреть в ОДНИ И ТЕ ЖЕ места: иначе доктор говорит «готово», а отправка
        падает «не задан канал» — или наоборот. Значение не логируется.
        """
        for key in keys:
            val = self.value(key)
            if val not in (None, ""):
                return val
            val = self.secret(key)
            if val not in (None, ""):
                return val
        return default

    def has_any(self, *keys):
        """Есть ли хоть одно значение — БЕЗ раскрытия (для doctor)."""
        return bool(self.any_value(*keys))


def config_for(client_id=None):
    """Фабрика конфига для клиента. Без client_id — клиент по умолчанию."""
    return ClientConfig(client_id)
