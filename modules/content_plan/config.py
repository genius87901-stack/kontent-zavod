# -*- coding: utf-8 -*-
"""Чтение конфига модуля content_plan — ЕДИНСТВЕННАЯ точка доступа.

Зеркалит контракт чтения story_publisher (CONTRACT.md §1, §3):
  * Корень установки = каталог с CONTRACT.md; все пути через pathlib от него.
  * Несекретный конфиг: config/factory.config.json (ключи = key из
    config_questions манифеста). Секреты — только из окружения (у этого модуля
    секретов нет, но интерфейс единый).
  * Нет файла/битый файл → пустой конфиг (урезанный режим, а не падение).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def install_root() -> Path:
    """Каталог с CONTRACT.md — корень установки. Идём вверх от этого файла."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CONTRACT.md").exists():
            return parent
    # запасной вариант: .../modules/content_plan/config.py -> parents[2]
    return here.parents[2]


def _config_path() -> Path:
    return install_root() / "config" / "factory.config.json"


def _load_config_file() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def config_value(key, default=None):
    """ЕДИНСТВЕННАЯ функция чтения НЕСЕКРЕТНОГО конфига."""
    return _load_config_file().get(key, default)


# --- секреты (витрина в телеграм) ------------------------------------------
#
# Секреты в коде запрещены. Читаем ровно тем же путём, что и остальной завод:
# сначала окружение, потом <корень установки>/.env (файл создаёт установщик,
# права 0600, в git не уезжает — install/env.example). Формат .env тот же, что
# разбирает tg_send.py и install/factory_config.py: KEY=значение, # — комментарий.

def _parse_env_file() -> dict:
    path = install_root() / ".env"
    out = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def secret(key: str, default=None):
    """Секретное значение по имени ключа. Порядок: окружение → .env.
    Возвращаемое значение НИКОГДА не печатается модулем — только факт наличия
    (см. has_secret) идёт в доктора и отчёты витрины."""
    val = os.environ.get(key)
    if val:
        return val
    val = _parse_env_file().get(key)
    return val if val else default


def has_secret(key: str) -> bool:
    """Есть ли секрет — БЕЗ раскрытия значения (для доктора и отчётов)."""
    return bool(secret(key))


# --- типизированные удобные чтения умолчаний планировщика ------------------

def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def plan_horizon_days(default=30) -> int:
    return _as_int(config_value("plan_horizon_days", default), default)


def posts_per_week(default=4) -> int:
    return _as_int(config_value("posts_per_week", default), default)


def veto_hours(default=12) -> int:
    return _as_int(config_value("veto_hours", default), default)


def default_platforms(default=("instagram", "telegram")):
    """Площадки по умолчанию: строка через запятую в конфиге или дефолт."""
    raw = config_value("plan_default_platforms")
    if isinstance(raw, str) and raw.strip():
        plats = [p.strip().lower() for p in raw.split(",") if p.strip()]
        if plats:
            return plats
    return list(default)


# --- количество контента на каждую площадку (per-platform volume) ----------
#
# Ключ ОПЦИОНАЛЕН. Нет ключа / мусор → None: планировщик падает на общий
# posts_per_week (прежнее поведение, обратная совместимость). Значение — сколько
# постов в неделю выпускать НА КАЖДУЮ площадку отдельно (у Telegram может быть
# свой темп, у Instagram свой).

def parse_platform_volume(raw):
    """Приводит «кол-во на площадку» к dict {площадка(lower): положительный int}.

    Принимает JSON-объект {"instagram": 5, "telegram": 7} ИЛИ строку
    "instagram:5, telegram:7". Мусор/пусто/нет валидных пар → None (тогда
    вызывающий берёт общий posts_per_week — прежнее поведение)."""
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, str) and raw.strip():
        items = []
        for part in raw.split(","):
            if ":" in part:
                k, _, v = part.partition(":")
                items.append((k, v))
    else:
        return None
    out = {}
    for k, v in items:
        plat = str(k).strip().lower()
        iv = _as_int(v, None)
        if plat and isinstance(iv, int) and iv > 0:
            out[plat] = iv
    return out or None


def default_per_platform_volume():
    """Умолчание «сколько постов в неделю на каждую площадку» из конфига
    (ключ plan_per_platform_volume). Нет ключа → None (общий posts_per_week)."""
    return parse_platform_volume(config_value("plan_per_platform_volume"))


#: см. script_writer.config — имя по умолчанию нейтральное, легаси-имя эксперта-
#: донора остаётся вторым кандидатом, чтобы его установка не потеряла книгу.
DEFAULT_BIBLE_NAME = "КНИГА_СМЫСЛОВ.md"
LEGACY_BIBLE_NAMES = ("CONTENT_BIBLE_Valeria_Chirkova.md",)


def bible_file(default=None) -> str:
    """Имя файла книги смыслов в корне установки — тот же ключ конфига
    (script_bible_file), что читает script_writer.config.bible_file(). Один
    ответ клиента на этапе настройки подключает книгу смыслов сразу к обоим
    модулям (BUG-2026-08-08: раньше у content_plan такого переключателя не
    было вообще, книгу смыслов другого клиента подключить было нельзя)."""
    raw = config_value("script_bible_file")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if default:
        return default
    try:
        root = install_root()
        for name in (DEFAULT_BIBLE_NAME,) + LEGACY_BIBLE_NAMES:
            if (root / name).is_file():
                return name
    except Exception:
        pass
    return DEFAULT_BIBLE_NAME


# --- фирменный шаблон каруселей (BOX-XX: carousel_bridge собирал только
# engine_photo, игнорируя выбор клиента по витрине BANK_SHABLONOV.jpg) --------

def carousel_registry() -> dict:
    """Реестр шаблонов каруселей (styles/carousel_templates.json) или {}.

    Нет файла / битый JSON → {} — контракт модуля (см. шапку файла): «нет
    данных» не роняет чтение, вызывающий работает в урезанном режиме."""
    p = install_root() / "styles" / "carousel_templates.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def carousel_template():
    """Фирменный шаблон каруселей клиента — {"family","accent","engine","title"}
    или None, если клиент его ещё не выбирал (тогда сборщик берёт дефолт).

    Зеркалит install/factory_config.py::carousel_template() тем же форматом
    хранения (ключ carousel_template = «семейство:акцент» в
    config/factory.config.json, пишет install/setup_config.py), но читает
    его СВОИМ путём (config_value), не межмодульным импортом install/ —
    контракт этого файла держит ЕДИНСТВЕННУЮ точку доступа к конфигу
    content_plan (см. шапку файла)."""
    raw = str(config_value("carousel_template") or "").strip().lower()
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

    # Записан мусор — трактуем как «не выбран», а не падаем.
    if family not in families or (accents and accent not in accents):
        return None

    f = families[family]
    return {"family": family, "accent": accent,
            "engine": str(f.get("engine", "")), "title": str(f.get("title", ""))}


def photos_dir():
    """Каталог входных фото для РЕАЛЬНОЙ сборки каруселей (несекретный путь из
    конфига content_plan_photos_dir). Пусто/нет ключа → None (сборщик тогда
    возьмёт фото из слота или банка движка). Относительный путь резолвим от
    корня установки — никаких абсолютных путей в коде/конфиге не требуется."""
    raw = config_value("content_plan_photos_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = Path(raw.strip())
    if not p.is_absolute():
        p = install_root() / p
    return p


# --- режим одобрения публикации (окно-вето / авто-одобрение) ---------------

# Порядок = самый безопасный первым. daily строже всего (ручное одобрение
# каждого выпуска). Горизонтные режимы авто-одобряют слоты в пределах N дней.
APPROVAL_MODES = ("daily", "week", "2weeks", "3weeks", "month")

# Горизонт авто-одобрения в днях. daily=0 — авто-одобрения нет вовсе (ручной режим).
_APPROVAL_HORIZON_DAYS = {
    "daily": 0,
    "week": 7,
    "2weeks": 14,
    "3weeks": 21,
    "month": 30,
}

DEFAULT_APPROVAL_MODE = "daily"


def publish_approval_mode(default=DEFAULT_APPROVAL_MODE) -> str:
    """Режим одобрения публикации из конфига. Управляет ТОЛЬКО логикой
    пред-разрешения слотов (окно-вето / авто-одобрение), НЕ реальной отправкой —
    жёсткий стоп в gate.py стоит поверх и ничего не публикует ни в одном режиме.

    Нет ключа / мусор / незнакомое значение → безопасный дефолт daily (ручное
    одобрение каждого выпуска). Так старый конфиг ведёт себя как раньше."""
    raw = config_value("publish_approval_mode", default)
    if not isinstance(raw, str):
        return default
    val = raw.strip().lower()
    return val if val in APPROVAL_MODES else default


def approval_horizon_days(mode=None) -> int:
    """Горизонт авто-одобрения в днях для режима (daily=0 — ручной, без авто)."""
    if mode is None:
        mode = publish_approval_mode()
    return _APPROVAL_HORIZON_DAYS.get(mode, 0)


# «Частота участия» человека в публикациях — человекочитаемая подпись режима.
# Тот же самый publish_approval_mode, только сформулированный как ЧАСТОТА
# УЧАСТИЯ (как часто клиента спрашивают/он подтверждает выход контента).
_PARTICIPATION_LABEL = {
    "daily": "участвую в каждом посте — подтверждаю каждый выпуск вручную",
    "week": "участвую раз в неделю — одобряю сразу на 7 дней вперёд",
    "2weeks": "участвую раз в 2 недели — одобряю сразу на 14 дней вперёд",
    "3weeks": "участвую раз в 3 недели — одобряю сразу на 21 день вперёд",
    "month": "участвую раз в месяц — одобряю сразу на 30 дней вперёд",
}


def participation_label(mode=None) -> str:
    """Подпись «частоты участия» для режима одобрения (для показа клиенту)."""
    if mode is None:
        mode = publish_approval_mode()
    return _PARTICIPATION_LABEL.get(mode, mode)


# --- время выдачи и часовой пояс (BOX-26) ----------------------------------
#
# Клиенты в разных зонах, поэтому время публикации хранится С ЗОНОЙ, а не как
# «наивное» местное. Смещения фиксированные и зашиты таблицей намеренно:
# у клиента Windows БЕЗ СЕТИ, а stdlib-`zoneinfo` на Windows требует пакет
# tzdata (базы IANA в системе нет) — на офлайн-коробке его может не оказаться.
# Все перечисленные зоны переход на летнее время НЕ соблюдают, поэтому
# фиксированное смещение для них точное. Если zoneinfo всё же доступен —
# берём его (он точнее и переживёт смену правил), таблица остаётся запасной.
TIMEZONES = {
    "Europe/Kaliningrad": "+02:00",
    "Europe/Minsk": "+03:00",
    "Europe/Moscow": "+03:00",
    "Europe/Samara": "+04:00",
    "Asia/Tbilisi": "+04:00",
    "Asia/Yerevan": "+04:00",
    "Asia/Dubai": "+04:00",
    "Asia/Yekaterinburg": "+05:00",
    "Asia/Almaty": "+05:00",
    "Asia/Tashkent": "+05:00",
    "Asia/Omsk": "+06:00",
    "Asia/Krasnoyarsk": "+07:00",
    "Asia/Bangkok": "+07:00",
    "Asia/Irkutsk": "+08:00",
    "Asia/Yakutsk": "+09:00",
    "Asia/Vladivostok": "+10:00",
    "Asia/Magadan": "+11:00",
    "Asia/Kamchatka": "+12:00",
}

DEFAULT_TIMEZONE = "Europe/Moscow"

# Время выдачи по умолчанию + предлагаемые варианты (choice-вопрос клиенту).
POST_TIME_OPTIONS = ("09:00", "12:00", "18:00", "21:00")
DEFAULT_POST_TIME = "09:00"


def parse_offset(raw):
    """«+03:00» / «+3» / «-05:30» → (часы, минуты) со знаком. Мусор → None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().replace("UTC", "").replace("utc", "").strip()
    if not s:
        return None
    sign = 1
    if s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:]
    if ":" in s:
        hh, _, mm = s.partition(":")
    else:
        hh, mm = s, "0"
    try:
        hours, minutes = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= hours <= 14 and 0 <= minutes < 60):
        return None
    return sign * hours, sign * minutes


def normalize_time(raw, default=None):
    """«9», «9:5», «21:00», «своё время (ЧЧ:ММ)» → «HH:MM» или default.

    Нужно потому, что вопрос про время — choice_or_text: клиент вправе назвать
    своё время, и в бриф может приехать что угодно, включая сам текст варианта."""
    fallback = default or DEFAULT_POST_TIME
    if not isinstance(raw, str):
        return fallback
    digits = raw.strip()
    # выдёргиваем первое похожее на время сочетание Ч(Ч):(ММ)
    cur = ""
    for ch in digits:
        if ch.isdigit() or ch == ":":
            cur += ch
        elif cur:
            break
    if not cur:
        return fallback
    hh, _, mm = cur.partition(":")
    try:
        hour, minute = int(hh), int(mm or 0)
    except ValueError:
        return fallback
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return "%02d:%02d" % (hour, minute)


def normalize_timezone(raw, default=None):
    """Имя зоны из таблицы или смещение «+ЧЧ:ММ». Мусор → зона по умолчанию."""
    fallback = default or DEFAULT_TIMEZONE
    if isinstance(raw, str):
        key = raw.strip()
        if key in TIMEZONES:
            return key
        parsed = parse_offset(key)
        if parsed is not None:
            h, m = parsed
            return "%s%02d:%02d" % ("-" if (h < 0 or m < 0) else "+",
                                    abs(h), abs(m))
    return fallback


def timezone_offset(name, default=None):
    """Смещение зоны в виде строки «+03:00». Принимает и имя из TIMEZONES, и
    само смещение («+05:30» — для клиента из зоны, которой нет в списке).
    Незнакомое значение → default (или смещение зоны по умолчанию)."""
    if isinstance(name, str):
        key = name.strip()
        if key in TIMEZONES:
            return TIMEZONES[key]
        parsed = parse_offset(key)
        if parsed is not None:
            h, m = parsed
            sign = "-" if (h < 0 or m < 0) else "+"
            return "%s%02d:%02d" % (sign, abs(h), abs(m))
    if default is not None:
        return default
    return TIMEZONES[DEFAULT_TIMEZONE]


def tzinfo_for(name):
    """datetime.tzinfo для зоны. Сначала zoneinfo (если база IANA доступна),
    иначе — фиксированное смещение из таблицы. Никогда не падает."""
    from datetime import timedelta, timezone as _tz
    if isinstance(name, str) and name.strip() in TIMEZONES:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name.strip())
        except Exception:
            pass  # нет tzdata (обычная ситуация на офлайн-Windows) — берём таблицу
    parsed = parse_offset(timezone_offset(name))
    h, m = parsed if parsed else (3, 0)
    return _tz(timedelta(hours=h, minutes=m), str(name or DEFAULT_TIMEZONE))


def default_timezone() -> str:
    raw = config_value("content_plan_timezone")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_TIMEZONE


def default_post_time() -> str:
    raw = config_value("content_plan_post_time")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_POST_TIME


# --- витрина: личный телеграм-канал клиента --------------------------------

SHOWCASE_TOKEN_KEY = "TG_BOT_TOKEN"          # тот же бот, что у tg_send.py
SHOWCASE_CHAT_KEYS = ("TG_SHOWCASE_CHAT_ID", "TG_CHAT_ID")


def showcase_token():
    """Токен бота витрины. Только из окружения/.env, в коде и логах его нет."""
    return secret(SHOWCASE_TOKEN_KEY)


def showcase_chat():
    """Канал-витрина: сначала персональный ключ TG_SHOWCASE_CHAT_ID, потом общий
    архивный TG_CHAT_ID (тот же, что у tg_send.py), потом несекретный конфиг."""
    for key in SHOWCASE_CHAT_KEYS:
        val = secret(key)
        if val:
            return val
    raw = config_value("content_plan_showcase_chat")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def live_publish_enabled() -> bool:
    """Внутренний замок реальной публикации. НЕ вопрос клиенту: держим выключенным
    до отдельной задачи интеграции с autopost. Значение true само по себе ничего
    не публикует — в прототипе стоит ещё и жёсткий стоп в gate.py."""
    val = config_value("content_plan_live_publish", False)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "да")


# --- пробные Reels (Instagram trial reels, BOX-обновление 14.08.2026) -------
#
# Instagram умеет показать Reels СНАЧАЛА только не-подписчикам и по просмотрам
# за 72 ч решить, показывать ли подписчикам (share_mode в autopost --trial).
# Нужен публичный аккаунт от 1000 подписчиков — иначе Instagram флаг молча
# игнорирует. Клиент решает это ОДИН РАЗ на распаковке (см. questions.py:
# trial_reels_mode/trial_reels_per_week/trial_reels_cadence), а не на каждый
# ролик — иначе "давай выложим пробным" превращается в лишний вопрос при
# каждом Reels.
TRIAL_REELS_MODES = ("off", "auto", "manual")
DEFAULT_TRIAL_REELS_MODE = "off"

# regularity / логика отбора, КАКИЕ именно Reels недели идут пробными.
TRIAL_REELS_CADENCES = ("every", "weekly", "new_format_only")
DEFAULT_TRIAL_REELS_CADENCE = "weekly"


def trial_reels_mode(default=DEFAULT_TRIAL_REELS_MODE) -> str:
    """off — обычные Reels всегда. auto/manual — как флаг autopost --trial
    (auto сам открывает подписчикам при хорошем отклике за 72ч, manual — решает
    клиент в приложении). Незнакомое значение -> безопасный off."""
    raw = config_value("trial_reels_mode", default)
    val = raw.strip().lower() if isinstance(raw, str) else default
    return val if val in TRIAL_REELS_MODES else default


def trial_reels_per_week(default=0) -> int:
    """Сколько Reels в неделю пускать пробными (0 = не пускать, даже если
    trial_reels_mode не off — количество тоже нужно явно попросить у клиента)."""
    return _as_int(config_value("trial_reels_per_week", default), default)


def trial_reels_cadence(default=DEFAULT_TRIAL_REELS_CADENCE) -> str:
    """every — каждый Reels недели пробный (в пределах per_week). weekly —
    только первые per_week по хронологии за ISO-неделю. new_format_only —
    как weekly, поэтому: детектор «это новый формат/тема» в прототипе не
    реализован (нужна история публикаций клиента), брать как weekly и не
    выдавать желаемое за сделанное."""
    raw = config_value("trial_reels_cadence", default)
    val = raw.strip().lower() if isinstance(raw, str) else default
    return val if val in TRIAL_REELS_CADENCES else default
