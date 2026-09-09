# -*- coding: utf-8 -*-
"""
health_report — суточная ОБЕЗЛИЧЕННАЯ сводка о здоровье коробки (BOX-31/P120).

ЗАЧЕМ. Коробка стоит на личном компьютере клиентки, и разработчик не знает о ней
НИЧЕГО: встала ли, дошла ли установка до конца, что сломалось. Клиентка в ошибках
не разберётся и чаще всего просто не напишет. Модуль раз в сутки отправляет
разработчику короткую техническую сводку, чтобы чинить молча — до того, как она
на поломку наткнётся.

⚖️ ПО УМОЛЧАНИЮ МОЛЧИТ. Ни одного байта наружу, пока согласие не получено и не
записано. Это не настройка и не пожелание — это устройство модуля (152-ФЗ).
Патч приезжает в уже стоящие коробки СПЯЩИМ; начать слать данные без спроса
нельзя. Состояния ровно три, и они различаются явно:

    unknown  — согласие ещё не спрашивали      → НЕ ОТПРАВЛЯЕМ
    granted  — согласие дано и записано        → отправляем
    denied   — клиент отказался                → НЕ ОТПРАВЛЯЕМ, больше не спрашиваем
    revoked  — согласие отозвано               → НЕ ОТПРАВЛЯЕМ, событие записано

ЧЕСТНО, А НЕ СКРЫТНО. Факт отправки написан клиенту человеческим языком в
`КАК_УСТАНОВИТЬ.md` и `CLAUDE.md`, полный текст согласия — в
`СОГЛАСИЕ_ДИАГНОСТИКА.md`. `--show` печатает РОВНО тот текст, который уйдёт.

ЧТО УХОДИТ (белый список `_SPEC`; перечень закрытый и совпадает с текстом согласия):
    версия коробки · пройденные этапы · результат каждого доктора (ок/не ок + КОД
    и КАТЕГОРИЯ, не сырой текст) · ОС и разрядность · версия Python · наличие
    ffmpeg и весов · последний патч · обезличенный id копии · метка времени ·
    отметка согласия (версия текста, его хеш, дата, способ).

ЧЕГО НЕ УХОДИТ НИКОГДА (три независимых рубежа, а не обещание):
    имена · почты · телефоны · тексты и смыслы клиента · названия её материалов ·
    пути (в т.ч. с именем пользователя) · содержимое .env · ключи и токены ·
    license.key · любые медиафайлы. Идентификатор копии — только SHA-256 из
    installer/machine_fingerprint.py, сырые id наружу не идут по устройству.

ГДЕ ЖИВЁТ ЗАПИСЬ СОГЛАСИЯ. `config/health_consent.json` (состояние) и
`config/health_consent.jsonl` (append-only журнал юридических событий). Именно в
`config/`: это ЗАПРЕТНАЯ ДЛЯ ПАТЧЕЙ зона (updater.py:74) — значит ни один будущий
патч не сможет ни подделать согласие, ни стереть отзыв. Писать туда во время
работы модуль имеет право (так же делает сам updater с box_version.json).

МОЛЧА И НЕ МЕШАЯ. `--auto` НЕ собирает сводку: он читает пару json (миллисекунды)
и, если пора, ОТСОЕДИНЯЕТ фоновый процесс и сразу выходит с кодом 0. Сбор
докторов идёт в отсоединённом потомке с пониженным приоритетом. Нет сети — тихо
вышли. Ошибка отправки — тихо вышли. Код возврата 0 всегда, кроме плохого ввода (2).

ЗАПУСК (клиентское):
    python health_report.py --auto            # для агента: тихо, не чаще раза в сутки
                                              # (пока согласие не спрашивали — печатает
                                              #  одну строку-подсказку и больше ничего)
    python health_report.py --show            # показать, что именно уйдёт (НЕ отправляет)
    python health_report.py --consent-text    # показать полный текст согласия
    python health_report.py --consent-grant   # согласие дано (записывается)
    python health_report.py --consent-deny    # отказ (записывается, больше не спрашиваем)
    python health_report.py --off             # отозвать согласие: отправка прекращается
    python health_report.py --status          # состояние согласия и отправок

Только stdlib. Без input(). Секретов не печатает.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: секрет канала. КОРЕНЬ установки, а не config/: `config/` — запретная зона для
#: патчей (updater.py:74), туда канал не довезти и не ротировать. В git этого
#: файла нет (см. health_channel.example.json и .gitignore).
CHANNEL_FILE = "health_channel.json"
#: состояние отправок (гейт «раз в сутки»). Внутри config/ — патч его не сдвинет.
STATE_FILE = "config/health_state.json"
#: ЗАМОК вокруг решения «спавнить сборщик или нет». Рядом с состоянием отправок и
#: намеренно в той же config/: замок обязан жить там же, где счётчики, которые он
#: защищает, — иначе на машине с недоступной config/ он «работал» бы вхолостую.
SPAWN_LOCK_FILE = "config/health_spawn.lock"
#: ЮРИДИЧЕСКАЯ ЧАСТЬ. Текущее состояние согласия и append-only журнал событий.
#: Оба в config/ намеренно: патч не может ни подделать согласие, ни стереть отзыв.
CONSENT_FILE = "config/health_consent.json"
CONSENT_LOG = "config/health_consent.jsonl"
#: текст согласия, который видит клиент. Лежит в корне установки, версионируется,
#: его хеш пишется в запись согласия — чтобы потом доказать, С ЧЕМ согласились.
CONSENT_TEXT_FILE = "СОГЛАСИЕ_ДИАГНОСТИКА.md"

#: три состояния плюс отказ. Отправка идёт РОВНО в одном из них.
CONSENT_STATES = ("unknown", "granted", "denied", "revoked")
#: каким действием получено согласие (закрытый перечень — идёт в отчёт).
CONSENT_METHODS = ("agent_chat", "installer", "cli")
#: сколько раз максимум спрашиваем, если клиент просто промолчал. Без напоминаний.
MAX_ASKS = 2

MIN_OK_HOURS = 20          # успешная отправка — не чаще раза в сутки
MIN_RETRY_HOURS = 6        # неудачная (офлайн) — можно повторить позже в тот же день
MAX_ATTEMPTS_DAY = 4       # жёсткий потолок попыток за календарные сутки
NET_TIMEOUT = 20           # сек на отправку
#: Сек на ВЕСЬ прогон докторов (в фоне, клиент не ждёт). 240 не хватало: у
#: run_doctors потолок 300 сек на ОДНОГО доктора, а их восемь, и на свежей
#: коробке первый прогон идёт с холодными импортами. Замер 10.08.2026 у клиента:
#: первый же отчёт пришёл с «доктора не запускались: timeout» — то есть данных о
#: здоровье в нём не было вообще (BOX-51).
DOCTORS_TIMEOUT = 600
#: Через сколько секунд замок сборщика считается ПРОТУХШИМ и снимается силой.
#: Отсчёт от DOCTORS_TIMEOUT, а не от круглого числа: замок держится ровно на
#: время жизни фонового сборщика, а самая долгая его часть — прогон докторов.
#: Запас сверху — на старт интерпретатора, отправку (NET_TIMEOUT) и запись файлов.
#: ЗАЧЕМ ВООБЩЕ ПРОТУХАНИЕ. Потомок отсоединён: его могут убить (перезагрузка,
#: антивирус, «завершить дерево процессов»), и снять замок ему будет уже нечем.
#: Без срока годности одна такая смерть заглушила бы сводки НАВСЕГДА — то есть
#: чинила бы одну беду (три сборщика) ценой худшей (полная тишина у клиента).
SPAWN_LOCK_STALE = DOCTORS_TIMEOUT + 300

# ── СТЕПЕНЬ БЕДЫ ДОКТОРА (BOX-51) ─────────────────────────────────────────────
# Контракт doctor (CONTRACT.md §2): 0 — готов, 2 — урезанный режим, 1 — не готов
# или сама проверка не смогла отработать. Раньше кода 2 не было, и любой ненулевой
# код означал и «не хватает токена площадки», и «модуль мёртв» — одинаково. Из-за
# этого phase() метил коробку как broken, а владелец получал 🔴 СРОЧНО на текст,
# в котором доктор своими словами писал «Это не поломка».
# Наружу НИЧЕГО НОВОГО не уходит: степень выводится из уже отправляемого `code`,
# состав payload не меняется ни на поле (перечень закрыт в СОГЛАСИЕ_ДИАГНОСТИКА.md).
DOCTOR_DEGRADED_CODE = 2


def doctor_level(code: int) -> str:
    """ok / degraded / down — по коду возврата доктора. Больше ниоткуда."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "down"
    if code == 0:
        return "ok"
    if code == DOCTOR_DEGRADED_CODE:
        return "degraded"
    return "down"


def is_down(d: dict) -> bool:
    """Доктор говорит «не работает» — только это и есть повод для тревоги."""
    return doctor_level(d.get("code", 1)) == "down"

# ── СРОЧНЫЕ СИГНАЛЫ (BOX-37) ──────────────────────────────────────────────────
# Отдельный от суточной сводки путь: поломку, которая мешает клиентке работать
# ПРЯМО СЕЙЧАС, нельзя придерживать до завтрашнего окна MIN_OK_HOURS. Правила
# намеренно узкие и детерминированные — как весь остальной модуль, ни намёка на
# эвристику "похоже на проблему". Считаются срочными РОВНО два случая:
#   * phase == "broken" — этап отмечен пройденным, а его доктор падает СЕЙЧАС;
#   * есть crash — сбор сам упал (см. build()).
# "stalled" НЕ входит: это «клиентка неделю не открывала завод», а не поломка —
# бросаться уведомлять владельца тут нечем помочь, ждёт человека, не техники.
URGENT_PHASES = ("broken",)
#: минимальный зазор между повторными срочными уведомлениями об ОДНОЙ И ТОЙ ЖЕ
#: беде — проблема не пропадёт из вида, но и не превратится в лавину при каждом
#: открытии Claude клиенткой в течение дня.
URGENT_MIN_GAP_HOURS = 2
#: минимальный зазор между попытками СОБРАТЬ (не обязательно отправить) сводку
#: специально ради проверки срочности, в обход суточного гейта due(). Именно
#: столько раз в день в худшем случае прогонятся доктора фоном — это и есть
#: цена скорости обнаружения, названная явно, а не спрятанная.
URGENT_CHECK_MIN_MINUTES = 30
#: жёсткий потолок срочных отправок за календарные сутки — предохранитель на
#: случай, если "беда" сама начнёт мигать (то падает, то чинится) быстрее, чем
#: работает URGENT_MIN_GAP_HOURS из-за смены сигнатуры на каждом прогоне.
MAX_URGENT_PER_DAY = 6

#: тестовая подмена транспорта. Тесты выставляют сюда заглушку — боевых запросов
#: при разработке не делаем НИКОГДА (предохранитель проекта).
SEND_HOOK = None


# ── общее ────────────────────────────────────────────────────────────────────

def find_root(explicit: str | None = None) -> Path:
    """Корень установки. Тот же контракт поиска, что в updater.find_root."""
    if explicit:
        return Path(explicit).resolve()
    for cand in (HERE, *HERE.parents):
        if (cand / "engine4.py").exists() or (cand / "modules").exists():
            return cand
    return HERE


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return True
    except OSError:
        return False


def _now() -> int:
    return int(time.time())


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def box_version(root: Path) -> str:
    return str(_read_json(root / "config" / "box_version.json", {}).get("version")
               or "1.0.0")


# ── СОГЛАСИЕ (152-ФЗ): текст, состояние, доказуемая запись ───────────────────

_VERSION_RE = re.compile(r"версия\s+текста\s*[:：]\s*([0-9][0-9A-Za-z.\-]{0,14})",
                         re.IGNORECASE)


def consent_text(root: Path) -> tuple[str, str, str]:
    """(полный текст, версия, sha256 текста).

    Хеш считается по БАЙТАМ файла на диске клиента: именно этот текст человек и
    видел. Версия читается из строки «Версия текста: X.Y» внутри самого файла —
    второй список версий рассинхронизировался бы с текстом при первой же правке.
    """
    p = root / CONSENT_TEXT_FILE
    try:
        raw = p.read_bytes()
    except OSError:
        return "", "", ""
    text = raw.decode("utf-8-sig", errors="replace")
    m = _VERSION_RE.search(text)
    return text, (m.group(1) if m else "0"), hashlib.sha256(raw).hexdigest()


def consent(root: Path) -> dict:
    c = _read_json(root / CONSENT_FILE, {})
    if not isinstance(c, dict):
        c = {}
    c.setdefault("state", "unknown")
    if c["state"] not in CONSENT_STATES:
        c["state"] = "unknown"
    c.setdefault("asked", 0)
    return c


def consent_state(root: Path) -> str:
    return consent(root)["state"]


def _append_log(root: Path, record: dict) -> None:
    """Append-only журнал: события согласия не переписываются НИКОГДА."""
    try:
        p = root / CONSENT_LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_consent(root: Path, event: str, how: str = "cli") -> dict:
    """Записать юридическое событие: согласие дано / отказ / отзыв.

    Пишем ДВА раза: текущее состояние (его читает гейт) и строку в append-only
    журнал (её предъявляют). Состав записи — то, чем согласие доказывается:
    точный текст (его версия и хеш), дата и время, обезличенный id копии,
    каким действием получено, версия коробки.
    """
    if event not in ("granted", "denied", "revoked"):
        event = "denied"
    if how not in CONSENT_METHODS:
        how = "cli"
    _text, ver, sha = consent_text(root)
    fp, _weak = _fingerprint()
    rec = {
        "schema": 1,
        "event": event,
        "ts": _utc(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "consent_version": ver,
        "consent_file": CONSENT_TEXT_FILE,
        "consent_sha256": sha,
        "copy": fp,
        "how": how,
        "box": box_version(root),
    }
    cur = consent(root)
    cur.update({"state": event, "asked": int(cur.get("asked", 0)),
                "last_event": rec})
    if event == "granted":
        cur["granted_at"] = rec["ts"]
    if event in ("revoked", "denied"):
        cur["stopped_at"] = rec["ts"]
    _write_json(root / CONSENT_FILE, cur)
    _append_log(root, rec)
    if event == "granted":
        # первый отчёт (с отметкой согласия) должен уйти сразу, а не через сутки:
        # доказательство согласия не должно лежать только на чужой машине.
        st = state(root)
        st["last_sent"] = 0
        st["last_attempt"] = 0
        save_state(root, st)
    return rec


def mark_asked(root: Path) -> int:
    """Отметить, что вопрос задан. Нужен, чтобы НЕ напоминать бесконечно."""
    c = consent(root)
    c["asked"] = int(c.get("asked", 0)) + 1
    c["asked_at"] = _utc()
    _write_json(root / CONSENT_FILE, c)
    return c["asked"]


def need_ask(root: Path) -> bool:
    """Пора ли задать вопрос. «Нет» — нормальный ответ, повторов почти нет."""
    _t, _v, sha = consent_text(root)
    if not sha:                       # нет текста согласия — спрашивать не о чем
        return False
    c = consent(root)
    return c["state"] == "unknown" and int(c.get("asked", 0)) < MAX_ASKS


#: Строка-подсказка агенту клиента. Печатается ТОЛЬКО из `--auto` и ТОЛЬКО при
#: состоянии `unknown`. Пишется агенту, а не человеку: человеку вопрос задаёт
#: агент своими словами по инструкции в CLAUDE.md.
ASK_HINT = ("KZ-HEALTH: согласие на техническую сводку ещё не спрашивали — "
            "задай клиенту ОДИН вопрос по инструкции (текст: "
            "health_report.py --consent-text), ответ запиши: --consent-grant "
            "--how agent_chat / --consent-deny --how agent_chat, и отметь "
            "заданный вопрос: --consent-asked.")


def ask_hint(root: Path) -> str:
    """Что напечатать в `--auto`, чтобы вопрос о согласии вообще прозвучал.
    Пустая строка — молчим (а это, как и раньше, поведение по умолчанию).

    ЗАЧЕМ. `--auto` при `unknown` делал ровно ничего: tick() → blocked_reason()
    → выход с нулём, ни байта, ни слова. Вопрос агент задавал только если сам
    вспоминал сходить в `--consent-state` — а он не вспоминал, и сводка не
    приходила НИКОГДА. Гейт согласия при этом остаётся ровно тем же: строка на
    экране не отправляет ничего и ничего не разрешает.

    Молчим полностью, если:
      * состояние НЕ `unknown` — на `denied`/`revoked` человек уже ответил, и
        повторно спрашивать нельзя ни намёком; на `granted` спрашивать не о чем;
      * вопрос уже задавали MAX_ASKS раз или текста согласия рядом нет
        (need_ask);
      * модуль выключен переменной окружения KZ_HEALTH — тогда согласие ни на
        что не влияет, и беспокоить человека незачем.
    """
    if str(os.environ.get("KZ_HEALTH", "")).strip().lower() in ("off", "0", "no"):
        return ""
    if consent_state(root) != "unknown":
        return ""
    if not need_ask(root):
        return ""
    return ASK_HINT


# ── канал и состояние отправок ───────────────────────────────────────────────

def channel(root: Path) -> dict:
    """Настройки канала. Токен НИКОГДА не печатаем и не логируем."""
    cfg = _read_json(root / CHANNEL_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {"transport": str(cfg.get("transport") or "telegram"),
            "token": str(cfg.get("token") or ""),
            "chat_id": str(cfg.get("chat_id") or ""),
            "enabled": bool(cfg.get("enabled", True))}


def describe_channel(ch: dict) -> str:
    """Описание канала БЕЗ токена и БЕЗ адреса чата."""
    if not ch["token"] or not ch["chat_id"]:
        return "канал не настроен (отчёты никуда не уходят)"
    return f"{ch['transport']}, приватный чат разработчика (ключ вложен)"


def state(root: Path) -> dict:
    st = _read_json(root / STATE_FILE, {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("last_sent", 0)
    st.setdefault("last_attempt", 0)
    st.setdefault("attempts_today", 0)
    st.setdefault("day", "")
    st.setdefault("seq", 0)
    # ── срочные сигналы (BOX-37), отдельные счётчики от суточной сводки ──
    st.setdefault("last_urgent_sig", "")     # сигнатура последней отправленной беды
    st.setdefault("last_urgent_sent", 0)     # когда её в последний раз слали
    st.setdefault("last_urgent_check", 0)    # когда в последний раз проверяли (не обязательно слали)
    st.setdefault("urgent_day", "")
    st.setdefault("urgent_today", 0)
    return st


def save_state(root: Path, st: dict) -> bool:
    return _write_json(root / STATE_FILE, st)


def blocked_reason(root: Path) -> str | None:
    """Почему отправка невозможна. None — можно. Проверяется ПЕРВЫМ делом.

    Порядок важен: согласие проверяется РАНЬШЕ канала и раньше всего остального.
    """
    if str(os.environ.get("KZ_HEALTH", "")).strip().lower() in ("off", "0", "no"):
        return "выключено переменной окружения KZ_HEALTH"
    st = consent_state(root)
    if st == "unknown":
        return "согласие ещё не спрашивали"
    if st == "denied":
        return "вы отказались — ничего не отправляется"
    if st == "revoked":
        return "согласие отозвано — ничего не отправляется"
    ch = channel(root)
    if not ch["enabled"]:
        return "канал отключён разработчиком"
    if not ch["token"] or not ch["chat_id"]:
        return "канал не настроен"
    return None


def due(root: Path) -> bool:
    """Пора ли отправлять. Успешная отправка — не чаще раза в сутки."""
    st, now = state(root), _now()
    if st.get("day") != _today():
        st["attempts_today"] = 0
    if int(st.get("attempts_today", 0)) >= MAX_ATTEMPTS_DAY:
        return False
    if now - int(st.get("last_sent", 0)) < MIN_OK_HOURS * 3600:
        return False
    if now - int(st.get("last_attempt", 0)) < MIN_RETRY_HOURS * 3600:
        return False
    return True


def mark_attempt(root: Path) -> None:
    """Отметить попытку ДО запуска сбора: зависший потомок не даёт лавины."""
    st = state(root)
    if st.get("day") != _today():
        st["day"], st["attempts_today"] = _today(), 0
    st["attempts_today"] = int(st.get("attempts_today", 0)) + 1
    st["last_attempt"] = _now()
    save_state(root, st)


def mark_sent(root: Path) -> None:
    st = state(root)
    st["last_sent"] = _now()
    st["seq"] = int(st.get("seq", 0)) + 1
    save_state(root, st)


def urgent_signature(payload: dict) -> str:
    """Короткая строка, описывающая ТЕКУЩУЮ беду — или "" если беды нет.

    Ключ дедупликации: та же сигнатура — то же самое "не чинили с прошлого
    раза", менять поведение не нужно. Другая сигнатура — новая беда (или
    старая ушла и пришла новая), эскалировать заново стоит немедленно.
    Строится ТОЛЬКО из уже прошедших белый список полей — вызывается после
    sanitize(), сам ничего личного видеть не может по построению.
    """
    if payload.get("crash"):
        c = payload["crash"] or {}
        return "crash:%s" % (c.get("type") or "?")
    if payload.get("phase") in URGENT_PHASES:
        # BOX-51: в сигнатуру идут ТОЛЬКО по-настоящему упавшие модули. Иначе
        # урезанный доктор, мигающий рядом со сломанным, менял бы сигнатуру и
        # каждый раз пробивал зазор URGENT_MIN_GAP_HOURS как «новая беда».
        failing = sorted({"%s=%s" % (d.get("m"), d.get("cat"))
                          for d in (payload.get("doctors") or [])
                          if not d.get("ok") and is_down(d)})
        return "broken:%s" % ",".join(failing[:6])
    return ""


def should_send_urgent(root: Path, sig: str) -> bool:
    """Отправлять ли СЕЙЧАС уже известную по сигнатуре беду.

    Новая сигнатура — да, всегда (в пределах суточного потолка). Та же самая —
    только если прошёл URGENT_MIN_GAP_HOURS: проблема не потеряется из вида,
    но и не устроит лавину на каждом открытии Claude клиенткой за день."""
    if not sig:
        return False
    st = state(root)
    today_count = int(st.get("urgent_today", 0)) if st.get("urgent_day") == _today() else 0
    if today_count >= MAX_URGENT_PER_DAY:
        return False
    if st.get("last_urgent_sig") != sig:
        return True
    return _now() - int(st.get("last_urgent_sent", 0)) >= URGENT_MIN_GAP_HOURS * 3600


def mark_urgent_sent(root: Path, sig: str) -> None:
    st = state(root)
    if st.get("urgent_day") != _today():
        st["urgent_day"], st["urgent_today"] = _today(), 0
    st["urgent_today"] = int(st.get("urgent_today", 0)) + 1
    st["last_urgent_sig"] = sig
    st["last_urgent_sent"] = _now()
    save_state(root, st)


def urgent_check_due(root: Path) -> bool:
    """Пора ли фоном пересобрать сводку РАДИ ПРОВЕРКИ срочности, не дожидаясь
    суточного гейта due(). Не путать с should_send_urgent — это гейт на СБОР
    (дорогая часть, доктора), а тот — гейт на ОТПРАВКУ уже собранного."""
    return _now() - int(state(root).get("last_urgent_check", 0)) >= URGENT_CHECK_MIN_MINUTES * 60


def mark_urgent_check(root: Path) -> None:
    st = state(root)
    st["last_urgent_check"] = _now()
    save_state(root, st)


# ── ЗАМОК СБОРЩИКА: один сборщик на коробку ──────────────────────────────────
# ЗАЧЕМ. Решение «пора собирать» состоит из ЧТЕНИЯ состояния (due/urgent_check_due)
# и ЗАПИСИ отметок (mark_attempt/mark_urgent_check). Между чтением и записью —
# зазор, и в него влезает вторая копия: у клиента Claude запускается несколько раз
# подряд (окно агента, run_doctors, ручной прогон), tick() зовётся из каждого.
# ДОКАЗАТЕЛЬСТВО, а не опасение: 17.08.2026 19:54:40Z от ОДНОЙ копии пришли ТРИ
# отчёта с одинаковыми ts, seq=10 и patches=10, но с РАЗНЫМИ результатами
# докторов — три сборщика гоняли докторов одновременно и мешали друг другу
# (общий .venv, общие временные файлы, конкуренция за ffmpeg и диск).
# Одними отметками в json это не лечится: сколько их ни переставляй, атомарности
# у пары «прочитал–записал» не появится. Нужен ОДИН неделимый шаг, и он тут ровно
# один: создание файла с O_CREAT|O_EXCL. Это атомарно и на NTFS, и на POSIX, не
# требует ни msvcrt, ни fcntl (целевая ОС — Windows, CONTRACT.md §3).
# Замок держится ВСЁ ВРЕМЯ сбора, а не только миг решения: три сборщика подряд —
# это и есть та беда, которую чиним, поэтому снимает замок сам потомок, закончив
# работу (collect_and_send), либо срок годности SPAWN_LOCK_STALE.

def _spawn_lock_path(root: Path) -> Path:
    return root / SPAWN_LOCK_FILE


def acquire_spawn_lock(root: Path) -> bool:
    """Взять замок. True — сборщик наш, можно спавнить. False — уже кто-то собирает.

    Ни одного исключения наружу: зовётся из tick(), который по контракту не
    бросает и не думает.
    """
    p = _spawn_lock_path(root)
    for attempt in (1, 2):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Замок занят. Два разных случая, и различает их ТОЛЬКО возраст файла:
            # либо сборщик честно работает, либо процесс умер и файл осиротел.
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                # Исчез между os.open и stat — значит прямо сейчас его кто-то
                # снимает. Не гонимся: следующий tick через полчаса разберётся.
                return False
            if age <= SPAWN_LOCK_STALE:
                return False
            if attempt == 2:
                return False           # сняли протухший, а место снова занято — уступаем
            try:
                p.unlink()
            except OSError:
                return False
            continue                   # второй заход: место освободилось, пробуем занять
        except OSError:
            # Замок не создаётся вовсе (config/ только на чтение, диск полон).
            # Тогда РАЗРЕШАЕМ сбор: прежнее поведение без замка — это дубли раз в
            # сутки, а запрет тут означал бы вечную тишину. Из двух зол выбираем
            # то, которое видно в телеграме, а не то, которого не видно нигде.
            return True
        try:
            # Содержимое — чисто для человека, разбирающего зависший замок руками.
            # Наружу не уходит НИКОГДА: payload собирается только по _SPEC.
            os.write(fd, ("pid %d, взят %s\n" % (os.getpid(), _utc())).encode("utf-8"))
        except OSError:
            pass
        finally:
            os.close(fd)
        return True
    return False


def release_spawn_lock(root: Path) -> None:
    """Снять замок. Нет файла — тоже успех: снимать нечего, а падать тут нельзя."""
    try:
        _spawn_lock_path(root).unlink()
    except OSError:
        pass


# ── сбор фактов ──────────────────────────────────────────────────────────────

def _fingerprint() -> tuple[str, bool]:
    """Обезличенный id копии: первые 16 hex из готового SHA-256 коробки.

    Свой отпечаток не выдумываем — берём installer/machine_fingerprint.py, где
    сырые id уже не покидают машину по устройству.
    """
    try:
        inst = str(HERE / "installer")
        if inst not in sys.path:
            sys.path.insert(0, inst)
        import machine_fingerprint as mf              # noqa: WPS433
        fp, weak = mf.fingerprint()
        return str(fp)[:16], bool(weak)
    except Exception:                                 # noqa: BLE001
        return "", True


def _venv_python(root: Path) -> Path | None:
    for p in (root / ".venv" / "Scripts" / "python.exe",
              root / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return None


# ── РЕАЛЬНАЯ РАСКЛАДКА .venv У КОРОБКИ (не та, что «по учебнику») ────────────
# ЧТО БЫЛО НЕ ТАК. Обе проверки ниже искали КАНОНИЧЕСКИЙ venv: пакеты в
# `.venv/Lib/site-packages`, версию питона в `.venv/pyvenv.cfg`. На Windows —
# основной ОС проекта (CONTRACT.md §2) — такого venv не существует В ПРИНЦИПЕ:
#
#   installer/offline_install.py:105 make_venv_from_embed() — `.venv` собирается
#   КОПИРОВАНИЕМ embeddable-Python: содержимое `python/` (тот самый
#   python-3.11.9-embed-amd64, кладётся инсталлятором, kontent-zavod.iss:61)
#   переносится целиком в `.venv/Scripts/`. Никакого `venv.EnvBuilder` — в
#   embeddable-дистрибутиве нет модулей `venv`/`ensurepip` (install/make_venv.py:55-58,
#   offline_install.py:63-67). А pyvenv.cfg пишет ИМЕННО EnvBuilder → его нет.
#
#   installer/offline_install.py:145 bootstrap_pip(py, py.parent, …) зовётся с
#   `py = .venv/Scripts/python.exe`, значит `py_dir = .venv/Scripts`, и site-packages
#   у него — `py_dir/"Lib"/"site-packages"`, то есть
#   → .venv/Scripts/Lib/site-packages  ←  СЮДА и распаковывается pip (строка 156),
#   сюда же он потом ставит все колёса: у embeddable-питона sys.prefix — это
#   каталог самого python.exe, а не папка уровнем выше.
#
# ЧТО ЭТО ДАВАЛО. Во ВСЕХ Windows-отчётах ffmpeg="none" и py_from="box" при живом
# зелёном докторе engine-media, который imageio_ffmpeg и импортирует, и запускает
# (modules/engine_media/doctor.py:230-246). То есть сводка врала ровно про то, ради
# чего её и читают: «нечем рендерить» на коробке, которая рендерит.
#
# ПОЧЕМУ СПИСОК, А НЕ ОДИН ПУТЬ. Раскладок реально три (Windows-коробка,
# macOS/Linux-ветка того же offline_install через venv.EnvBuilder, и dev-checkout
# с обычным `python -m venv`), плюс будущая может отличаться снова. Поэтому
# кандидаты перечислены явно, а последним рубежом стоит вопрос самому
# интерпретатору коробки — он отвечает про СЕБЯ и не может ошибиться раскладкой.

def _site_packages_dirs(root: Path) -> list[Path]:
    """Все места, где у коробки могут лежать pip-пакеты. Порядок = от частого к редкому."""
    venv = root / ".venv"
    out = [
        venv / "Scripts" / "Lib" / "site-packages",   # Windows: копия embeddable-Python
        venv / "Lib" / "site-packages",               # Windows: обычный venv (dev-машина)
    ]
    for base in (venv, venv / "Scripts"):             # macOS/Linux: минор заранее неизвестен
        try:
            out += sorted(base.glob("lib/python*/site-packages"))
        except OSError:
            continue
    return out


#: Ответ интерпретатора коробки на процесс. Спрашиваем максимум один раз: подпроцесс
#: дорог, а оба вопроса (версия и пакет) нужны в одном и том же сборе.
_VENV_PROBE: dict[str, dict] = {}
#: Сек на этот вопрос. Не 1-2: у embeddable-питона холодный старт на слабой машине
#: клиентки заметный. Живём в фоновом потомке, клиент всё равно не ждёт.
VENV_PROBE_TIMEOUT = 20

#: РОВНО ДВА факта и ничего больше. Ни путей, ни списка пакетов: то, что нельзя
#: отправить, лучше и не спрашивать — тогда оно не сможет утечь даже по ошибке.
_PROBE_CODE = (
    "import json,sys\n"
    "try:\n"
    "    import importlib.util as u\n"
    "    f = u.find_spec('imageio_ffmpeg') is not None\n"
    "except Exception:\n"
    "    f = False\n"
    "print(json.dumps({'v': '%d.%d.%d' % sys.version_info[:3], 'ffmpeg': f}))\n"
)


def _is_self_venv(root: Path) -> bool:
    """Мы САМИ запущены питоном из .venv коробки?

    Штатный случай: агент зовёт `.venv\\Scripts\\python.exe health_report.py --auto`
    (CLAUDE.md коробки). Тогда спрашивать некого — мы и есть ответ, а подпроцесс
    был бы чистой тратой секунд на машине клиентки.
    """
    vpy = _venv_python(root)
    if vpy is None:
        return False
    try:
        return (os.path.normcase(str(Path(sys.executable).resolve()))
                == os.path.normcase(str(vpy.resolve())))
    except OSError:
        return False


def _ask_venv(root: Path) -> dict:
    """Спросить интерпретатор коробки о нём самом. {} — спросить не вышло.

    Последний рубеж обеих проверок: раскладку каталогов можно не угадать, а вот
    ответ самого питона про свою версию и свой импорт неопровержим.
    """
    key = str(root)
    if key in _VENV_PROBE:
        return _VENV_PROBE[key]
    ans: dict = {}
    vpy = _venv_python(root)
    if vpy is None:
        _VENV_PROBE[key] = ans
        return ans
    if _is_self_venv(root):
        try:
            import importlib.util                      # noqa: WPS433
            ans = {"v": ".".join(str(x) for x in sys.version_info[:3]),
                   "ffmpeg": importlib.util.find_spec("imageio_ffmpeg") is not None}
        except Exception:                              # noqa: BLE001
            ans = {}
        _VENV_PROBE[key] = ans
        return ans
    try:
        env = dict(os.environ)
        env["KZ_HEALTH_CHILD"] = "1"                   # на всякий случай: не зациклиться
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [str(vpy), "-c", _PROBE_CODE], cwd=str(root), env=env,
            timeout=VENV_PROBE_TIMEOUT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        if isinstance(data, dict):
            ans = data
    except Exception:                                  # noqa: BLE001
        ans = {}                                       # молча: диагностика не имеет права мешать
    _VENV_PROBE[key] = ans
    return ans


def _py_version(root: Path) -> tuple[str, str]:
    """(версия, откуда: venv/box/current).

    «venv» — питон окружения завода (им и работает коробка), «box» — какой-то
    другой питон из папки завода, «current» — системный. Раньше «venv» на Windows
    не получался НИКОГДА: единственным признаком был pyvenv.cfg, которого при
    embeddable-раскладке не бывает (см. блок выше), и все отчёты клиентов уезжали
    с py_from="box".
    """
    cfg = root / ".venv" / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("version"):
                v = line.split("=", 1)[1].strip()
                if v:
                    return v, "venv"
    except (OSError, IndexError):
        pass
    # pyvenv.cfg нет — это норма для Windows-коробки, а не признак отсутствия venv.
    if _venv_python(root) is not None:
        v = _ask_venv(root).get("v")
        if v:
            return str(v)[:16], "venv"
    v = ".".join(str(x) for x in sys.version_info[:3])
    in_box = str(HERE) in str(Path(sys.executable).resolve().parent)
    return v, ("box" if in_box else "current")


def _ffmpeg_kind(root: Path) -> str:
    """system / pip / none. Системный ffmpeg необязателен: движок берёт из pip.

    «pip» ищется по РЕАЛЬНЫМ раскладкам (_site_packages_dirs), а если ни одна не
    подошла — спрашиваем сам интерпретатор коробки: сможет ли он импортировать
    imageio_ffmpeg. Именно это делает доктор engine-media, чей зелёный статус и
    вскрыл враньё прежней проверки.
    """
    try:
        import shutil                                  # noqa: WPS433
        if shutil.which("ffmpeg"):
            return "system"
    except Exception:                                  # noqa: BLE001
        pass
    for site in _site_packages_dirs(root):
        try:
            if (site / "imageio_ffmpeg").is_dir():
                return "pip"
        except OSError:
            continue
    if _ask_venv(root).get("ffmpeg"):
        return "pip"
    return "none"


def _models_present(root: Path) -> bool:
    """Веса моделей: не просто папка, а хоть один крупный файл в ней."""
    d = root / ".cache_models"
    if not d.is_dir():
        return False
    try:
        for p in d.rglob("*"):
            if p.is_file() and p.stat().st_size > 1_000_000:
                return True
    except OSError:
        pass
    return False


def _load_stages_module(root: Path):
    """install/stages.py как модуль (он не пакет и не лежит на PYTHONPATH)."""
    try:
        import importlib.util                          # noqa: WPS433
        cand = root / "install" / "stages.py"
        if not cand.is_file():
            return None
        spec = importlib.util.spec_from_file_location("factory_stages", cand)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:                                  # noqa: BLE001
        return None


def _stages(root: Path) -> tuple[list[int], int | None, dict]:
    """(пройденные этапы, следующий, {этап: {модули}}). Правду берём у stages.py."""
    mod = _load_stages_module(root)
    if mod is not None:
        try:
            done = [int(x) for x in mod.done_stages(root)]
            nxt = mod.next_stage(root)
            mods = {sid: set(mod.modules_for_stage(root, sid))
                    for sid in mod.stage_ids(root)}
            return done, (int(nxt) if nxt is not None else None), mods
        except Exception:                              # noqa: BLE001
            pass
    # запасной путь: те же два файла напрямую
    done = sorted(int(x) for x in
                  (_read_json(root / "install" / ".state.json", {}) or {}).get("stages_done", [])
                  if str(x).isdigit())
    ids = [int(s["id"]) for s in
           (_read_json(root / "install" / "stages.json", {}) or {}).get("stages", [])
           if str(s.get("id", "")).isdigit()]
    nxt = next((i for i in sorted(ids) if i not in done), None)
    return done, nxt, {}


def _idle_days(root: Path) -> int:
    """Сколько дней ничего не двигалось: по свежести следов установки."""
    newest = 0.0
    for rel in ("install/.state.json", "config/factory.config.json",
                "config/box_version.json", "config/update_log.jsonl"):
        p = root / rel
        try:
            if p.exists():
                newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    if not newest:
        return -1
    return max(0, int((time.time() - newest) // 86400))


# ── доктора: категория вместо сырого текста ──────────────────────────────────

#: закрытый перечень категорий. Наружу уходит ТОЛЬКО слово отсюда.
_CATEGORIES = (
    "ok", "no_drive", "no_write", "no_config", "no_keys", "no_ffmpeg",
    "no_model", "no_deps", "broken", "timeout", "unrunnable", "partial", "other",
)

#: (категория, ключевые слова в НИЖНЕМ регистре). Порядок = приоритет: от
#: частного к общему. Сам текст доктора при этом никуда не уходит.
_CATEGORY_RULES = (
    ("broken",    ("сбой", "повреждён", "повреждена", "не запускается")),
    ("no_deps",   ("no module named", "не найден пакет", "не установлен",
                   "modulenotfound", "importerror", "пакет отсутствует")),
    ("no_ffmpeg", ("ffmpeg",)),
    ("no_model",  ("модел", "весов", "веса", "whisper")),
    ("no_write",  ("записать", "запись", "нет прав")),
    ("no_drive",  ("google диск", "гугл диск", "контентзавод", "папк")),
    ("no_keys",   ("ключ", "токен", "не заполнено", "площадк", "логин", "аккаунт")),
    ("no_config", ("не указан", "не настроен", "конфиг", "config")),
    ("partial",   ("урезанн", "не хватает", "доделать", "поправить")),
)


#: Строка доктора, которая рассказывает про УСПЕХ, а не про беду. Такие строки
#: из разбора выбрасываются: доктор публикации печатает «OK  FFmpeg: на месте.»
#: — и по ключевому слову «ffmpeg» вся его беда становилась `no_ffmpeg`, даже
#: когда на самом деле не заполнены ключи площадок (BOX-51).
_OK_LINE_RE = re.compile(r"^\s*(?:ok\b|[✓✔+]\s|—?\s*всё\b)", re.IGNORECASE)


#: Заголовок раздела ПОДСКАЗОК. Всё, что ниже него, про беду не говорит вовсе —
#: доктор сам пишет «работать не мешают».
#:
#: 07.09.2026, ЗАМЕР ПО ВЫГРУЗКЕ КАНАЛА ЗДОРОВЬЯ. Это второе издание BOX-51, и
#: оно жило дольше первого. Все три копии на 1.3.59 присылали владельцу
#: `engine-media=no_ffmpeg`, имея В ТОЙ ЖЕ строке отчёта пометку «ffmpeg pip» —
#: то есть ffmpeg у них СТОИТ. Категорию давала подсказка доктора «Системный
#: ffmpeg в PATH не найден — ЭТО НЕ ПРОБЛЕМА: рендер берёт ffmpeg из пакета»:
#: правило `no_ffmpeg` ищет слово «ffmpeg» где угодно в тексте и стоит третьим
#: по приоритету, поэтому перебивало и `no_keys`, и `no_model`. Настоящая беда
#: пряталась за выдуманной, а владелец читал «нет ffmpeg» и не понимал, почему
#: установка ffmpeg ничего не меняет — её и не надо было ставить.
_HINTS_HEAD_RE = re.compile(
    r"^\s*(?:подсказк\w*|советы|замечани\w*|notes)\b", re.IGNORECASE)

#: Строка, которая САМА себя объявляет не бедой — такие доктора пишут и вне
#: раздела подсказок.
_NOT_A_PROBLEM_RE = re.compile(
    r"(?:не проблема|работать не меша|необязательн|не обязателен|"
    r"запасной вариант|просто удобство)", re.IGNORECASE)


def _problem_text(text: str) -> str:
    """Оставить только строки про беду. Категория считается по НИМ.

    Отсекаем три вида шума: строки про успех (`ok`, галочка), ВЕСЬ хвост после
    заголовка «Подсказки» и отдельные строки, которые сами говорят, что бедой
    не являются. Без этого одна доброжелательная подсказка меняла диагноз всего
    модуля — разбор в комментарии к _HINTS_HEAD_RE.
    """
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if _HINTS_HEAD_RE.match(line):
            break                      # ниже только подсказки, беды там нет
        if _OK_LINE_RE.match(line) or _NOT_A_PROBLEM_RE.search(line):
            continue
        out.append(line)
    return "\n".join(out)


def category(code: int, text: str) -> str:
    """Код+текст доктора → одно слово из _CATEGORIES. Текст НАРУЖУ НЕ ИДЁТ.

    Смысл функции ровно в этом: доктор печатает «Папка Google Диска указана, но
    на этом компьютере её нет: C:\\Users\\Марина\\…» — а мы отправляем «no_drive».
    """
    if code == 0:
        return "ok"
    if code == 124:
        return "timeout"
    if code == 127:
        return "unrunnable"
    low = _problem_text(text).lower()
    for cat, words in _CATEGORY_RULES:
        if any(w in low for w in words):
            return cat
    return "other"


def run_doctors(root: Path) -> tuple[list[dict], str]:
    """Сводка по докторам. Правду берём у install/run_doctors.py, не выдумываем.

    Возвращает ([{m, ok, code, cat}], причина_пропуска). Сырой русский текст
    докторов используется ТОЛЬКО чтобы вычислить категорию, и тут же выбрасывается.
    """
    script = root / "install" / "run_doctors.py"
    if not script.is_file():
        return [], "no_script"
    if _venv_python(root) is None:
        # Окружения нет — доктора всё равно упадут на импортах, а 8 подпроцессов
        # впустую займут машину клиента. Отсутствие .venv само по себе — ответ.
        return [], "no_venv"
    env = dict(os.environ)
    env["KZ_HEALTH_CHILD"] = "1"          # чтобы хук в run_doctors не зациклился
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--json", "--root", str(root)],
            cwd=str(root), env=env, timeout=DOCTORS_TIMEOUT,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return [], "timeout"
    except (OSError, subprocess.SubprocessError):
        return [], "unrunnable"
    try:
        data = json.loads(proc.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return [], "bad_output"
    out = []
    for r in data.get("results", []) or []:
        code = int(r.get("code", 1) or 0)
        out.append({"m": str(r.get("module", "")), "ok": bool(r.get("ok")),
                    "code": code, "cat": category(code, str(r.get("text", "")))})
    return out, ""


# ── фаза: «ещё не начала» ≠ «сломалось» ──────────────────────────────────────

def phase(root: Path, done: list[int], nxt: int | None, stage_mods: dict,
          doctors: list[dict], has_venv: bool, idle: int) -> str:
    """Одно слово, ради которого владелец и читает сводку.

    not_started — коробка встала, но клиентка не написала «установи»;
    installing  — установка идёт;
    ready       — все этапы пройдены и НИ ОДИН доктор не лежит (код 1/124/127);
    broken      — этап ОТМЕЧЕН пройденным, но его же доктор падает → чиним;
    stalled     — не начала/не доделала и неделю ничего не трогала.

    ⚠️ «ready» НЕ ЗНАЧИТ «ВСЁ РАБОТАЕТ», и докстринг это раньше обещал зря.
    Доктора в УРЕЗАННОМ режиме (код 2: нет ключей площадки, не подключён платный
    ручной инструмент) в расчёт слова не входят СОЗНАТЕЛЬНО — так решено в BOX-51,
    иначе владельцу летели красные тревоги на текст «это не поломка». Но следствие
    оказалось не менее вредным: копия e3d5c428769eb9b6 пять раз подряд отчиталась
    «ready», хотя publishing не мог публиковать (no_keys), а niche-hooks был урезан.
    Владелец видел зелёный статус у коробки, которая не делает главную работу.

    ПОЧЕМУ ЭТО НЕ ЧИНИТСЯ НОВЫМ СЛОВОМ (например "limited"). Перечень значений
    закрыт документом согласия: СОГЛАСИЕ_ДИАГНОСТИКА.md, п.9 — «одно слово с
    оценкой состояния ("не начата", "идёт", "готово", "сломалось",
    "остановилась")». Пять слов, скобки закрыты, а сам документ начинается с
    «Что именно отправляется — полный и закрытый перечень». Клиентка согласилась
    на эти пять; шестое — уже не то, на что она соглашалась, и добавлять его
    нельзя ни в _SPEC, ни в payload.
    Поэтому «работает всё» и «работает главное» разводятся ТАМ, где это ничего не
    отправляет: в человеческой шапке сообщения — см. phase_human() и render().
    """
    def norm(names) -> set:
        return {str(m).lower().replace("-", "_") for m in names}

    failed = norm(d["m"] for d in doctors if not d["ok"] and is_down(d))
    if failed and stage_mods:
        for sid in done:
            if failed & norm(stage_mods.get(sid, set())):
                return "broken"
    if not done and not has_venv:
        return "stalled" if idle >= 7 else "not_started"
    if nxt is not None:
        return "stalled" if idle >= 7 else "installing"
    return "ready" if not failed else "broken"


# ── payload ──────────────────────────────────────────────────────────────────

def collect(root: Path) -> dict:
    """Собрать СЫРУЮ сводку. Санитайзер ниже всё равно пропустит только белый список."""
    fp, weak = _fingerprint()
    done, nxt, stage_mods = _stages(root)
    doctors, skipped = run_doctors(root)
    py, py_from = _py_version(root)
    idle = _idle_days(root)
    has_venv = _venv_python(root) is not None

    last_patch, patches = "", 0
    try:
        lines = [l for l in (root / "config" / "update_log.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        patches = len(lines)
        if lines:
            last_patch = str(json.loads(lines[-1]).get("version", ""))
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    c = consent(root)
    ev = c.get("last_event") or {}
    return {
        "schema": 1,
        "ts": _utc(),
        "copy": fp,
        "fp_weak": weak,
        "seq": int(state(root).get("seq", 0)) + 1,
        # ── доказательство согласия едет в КАЖДОМ отчёте ──
        "consent": {"v": ev.get("consent_version", ""),
                    "sha": ev.get("consent_sha256", ""),
                    "ts": ev.get("ts", ""),
                    "how": ev.get("how", "")},
        "box": box_version(root),
        "patch": last_patch,
        "patches": patches,
        "client_build": (root / "config" / ".client_build").exists(),
        "os": platform.system(),
        "os_release": platform.release(),
        "bits": platform.architecture()[0] or "",
        "arch": platform.machine(),
        "py": py,
        "py_from": py_from,
        "venv": has_venv,
        "ffmpeg": _ffmpeg_kind(root),
        "models": _models_present(root),
        "stages_done": done,
        "stage_next": nxt,
        "idle_days": idle,
        "phase": phase(root, done, nxt, stage_mods, doctors, has_venv, idle),
        "doctors": doctors,
        "doctors_skipped": skipped,
        "crash": None,
        "urgent": False,   # выставляется отправителем ПОСЛЕ build(), не здесь
    }


# ── БЕЛЫЙ СПИСОК: рубеж 1 ────────────────────────────────────────────────────

#: Разрешённые символы строкового поля. Пути (`/`, `\`), почты (`@`) и токены
#: (`=`, длина) по этому шаблону не проходят ФИЗИЧЕСКИ.
_SAFE_STR = re.compile(r"^[0-9A-Za-zА-Яа-яЁё _.\-+:]{0,64}$")


def _s(v, allowed: tuple[str, ...] | None = None) -> str:
    t = str(v or "").strip()
    if allowed is not None:
        return t if t in allowed else ""
    return t if _SAFE_STR.match(t) else ""


def _i(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _doctors(v) -> list:
    out = []
    for item in (v or [])[:20]:
        if not isinstance(item, dict):
            continue
        out.append({"m": _s(item.get("m"))[:24], "ok": bool(item.get("ok")),
                    "code": _i(item.get("code")),
                    "cat": _s(item.get("cat"), _CATEGORIES) or "other"})
    return out


def _consent_block(v) -> dict:
    """Отметка согласия: версия текста, его хеш, дата, способ. Ничего больше."""
    if not isinstance(v, dict):
        v = {}
    sha = _s(v.get("sha"))
    return {"v": _s(v.get("v"))[:16],
            "sha": sha if re.fullmatch(r"[0-9a-f]{64}", sha or "") else "",
            "ts": _s(v.get("ts"))[:24],
            "how": _s(v.get("how"), CONSENT_METHODS)}


def _crash(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    return {"type": _s(v.get("type"))[:40], "where": _s(v.get("where"))[:40]}


#: ЕДИНСТВЕННЫЙ источник правды о составе отправки. Чего нет в этой таблице —
#: того нет в payload: sanitize() строит НОВЫЙ словарь только по её ключам.
#: Перечень ЗАКРЫТЫЙ и обязан совпадать с текстом СОГЛАСИЕ_ДИАГНОСТИКА.md.
_SPEC = {
    "schema":          lambda v: 1,
    "ts":              lambda v: _s(v),
    "copy":            lambda v: _s(v)[:16],
    "fp_weak":         bool,
    "seq":             _i,
    "consent":         _consent_block,
    "box":             lambda v: _s(v)[:16],
    "patch":           lambda v: _s(v)[:16],
    "patches":         _i,
    "client_build":    bool,
    "os":              lambda v: _s(v, ("Windows", "Darwin", "Linux")),
    "os_release":      lambda v: _s(v)[:16],
    "bits":            lambda v: _s(v, ("32bit", "64bit")),
    "arch":            lambda v: _s(v)[:16],
    "py":              lambda v: _s(v)[:16],
    "py_from":         lambda v: _s(v, ("venv", "box", "current")),
    "venv":            bool,
    "ffmpeg":          lambda v: _s(v, ("system", "pip", "none")),
    "models":          bool,
    "stages_done":     lambda v: [_i(x) for x in (v or [])[:10]],
    "stage_next":      lambda v: None if v is None else _i(v),
    "idle_days":       _i,
    "phase":           lambda v: _s(v, ("not_started", "installing", "ready",
                                        "broken", "stalled")) or "other",
    "doctors":         _doctors,
    "doctors_skipped": lambda v: _s(v, ("", "no_venv", "no_script", "timeout",
                                        "unrunnable", "bad_output")),
    "crash":           _crash,
    "urgent":          bool,   # BOX-37: срочный путь мимо суточного гейта
}


def sanitize(raw: dict) -> dict:
    """Рубеж 1: собрать payload ЗАНОВО, только из полей белого списка."""
    out = {}
    for key, fn in _SPEC.items():
        try:
            out[key] = fn(raw.get(key))
        except Exception:                              # noqa: BLE001
            out[key] = None
    return out


# ── ПОСЛЕДНИЙ РУБЕЖ: рубеж 3 ─────────────────────────────────────────────────

_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")


def assert_clean(payload) -> list[str]:
    """Рубеж 3: обойти готовый payload и найти всё, что похоже на личное.

    Непустой список = НЕ ОТПРАВЛЯЕМ. Лучше не отчитаться, чем увезти лишнее.
    """
    bad: list[str] = []

    def walk(node, path="") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            if len(node) > 64:
                bad.append(f"{path}: длинная строка")
            if any(ch in node for ch in ("/", "\\", "@")) or _DRIVE_RE.search(node):
                bad.append(f"{path}: похоже на путь или адрес")
        elif node is not None and not isinstance(node, (int, float, bool)):
            bad.append(f"{path}: недопустимый тип")

    walk(payload)
    return bad


def build(root: Path) -> tuple[dict, list[str]]:
    """Полный конвейер: собрать → просеять → проверить. Ошибка сбора не роняет."""
    try:
        raw = collect(root)
    except Exception as e:                             # noqa: BLE001
        raw = {"schema": 1, "ts": _utc(), "copy": _fingerprint()[0],
               "box": box_version(root), "phase": "broken",
               "crash": {"type": type(e).__name__, "where": "collect"}}
    payload = sanitize(raw)
    return payload, assert_clean(payload)


# ── текст сообщения ──────────────────────────────────────────────────────────

def phase_human(payload: dict) -> str:
    """Фаза СЛОВАМИ ДЛЯ ЧЕЛОВЕКА (только для текста сообщения, не для payload).

    ЗАЧЕМ. В payload у фазы пять разрешённых значений, и «ready» среди них — самое
    сильное: владелец читает его как «коробка работает». А код возвращает «ready»
    и тогда, когда часть модулей в урезанном режиме (см. phase()) — то есть когда
    завод, например, не может публиковать. Добавить шестое слово нельзя: перечень
    закрыт согласием (СОГЛАСИЕ_ДИАГНОСТИКА.md, п.9). Но ТЕКСТ сообщения согласием
    не ограничен — он собирается из уже отправляемых полей и никуда сверх payload
    не уходит. Здесь и разводим два разных состояния.

    Правится ТОЛЬКО шапка: `payload["phase"]` остаётся ровно тем, чем был, и
    санитайзер с рубежом 3 работают как раньше.
    """
    ph = str(payload.get("phase") or "")
    doctors = payload.get("doctors") or []
    soft = [str(d.get("m") or "?") for d in doctors
            if not d.get("ok") and not is_down(d)]
    if ph != "ready" or not soft:
        return ph
    # Первая строка — это то, что видно в свёрнутом уведомлении на телефоне.
    # Значит именно здесь слово «ready» не имеет права стоять в одиночестве.
    names = ", ".join(sorted(soft)[:4])
    return ("ready, НО РАБОТАЕТ НЕ ВСЁ — урезаны %d из %d (%s)"
            % (len(soft), len(doctors), names))


def render(payload: dict) -> str:
    """Короткая шапка для человека + сам payload. Без parse_mode — никакой разметки."""
    doctors = payload.get("doctors") or []
    # BOX-51: две разные беды — двумя разными словами. «6/8 ок · publishing=…»
    # читалось как поломка и тогда, когда модуль просто ждёт ключей.
    down = [f"{d['m']}={d['cat']}" for d in doctors if not d.get("ok") and is_down(d)]
    soft = [f"{d['m']}={d['cat']}" for d in doctors
            if not d.get("ok") and not is_down(d)]
    bad = down + soft
    con = payload.get("consent") or {}
    head = []
    if payload.get("urgent"):
        # Первая строка — то, что видно в свёрнутом уведомлении на телефоне до
        # разворачивания сообщения. Должна кричать сама по себе, без контекста.
        head.append("🔴 СРОЧНО · копии %s требуется внимание прямо сейчас"
                    % (payload.get("copy") or "?"))
    head += [
        f"KZ health · копия {payload.get('copy') or '?'} · {phase_human(payload)}",
        f"коробка {payload.get('box')}"
        + (f" (патч {payload['patch']})" if payload.get("patch") else "")
        + f" · {payload.get('os')} {payload.get('os_release')} {payload.get('bits')}"
        + f" · py {payload.get('py')}",
        f"этапы: {payload.get('stages_done')} → {payload.get('stage_next')}"
        f" · простой {payload.get('idle_days')} дн"
        f" · ffmpeg {payload.get('ffmpeg')} · веса {'да' if payload.get('models') else 'нет'}",
        f"согласие: версия {con.get('v') or '?'} от {con.get('ts') or '?'}"
        f" ({con.get('how') or '?'}), sha {(con.get('sha') or '')[:12]}",
    ]
    if doctors:
        line = f"доктора: {len(doctors) - len(bad)}/{len(doctors)} ок"
        if down:
            line += " · НЕ РАБОТАЮТ: " + ", ".join(down[:8])
        if soft:
            line += " · урезаны: " + ", ".join(soft[:8])
        head.append(line)
    elif payload.get("doctors_skipped"):
        head.append(f"доктора не запускались: {payload['doctors_skipped']}")
    if payload.get("crash"):
        head.append(f"падение: {payload['crash'].get('type')} в {payload['crash'].get('where')}")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(head) + "\n\n" + body[:3000]


# ── транспорт ────────────────────────────────────────────────────────────────

def _ssl_ctx() -> ssl.SSLContext:
    """Контекст проверки сертификата: корни certifi ПОВЕРХ системных.

    Та же поломка, что съела канал обновлений: у портативного питона коробки
    системного хранилища корней может не быть, и отправка сводки падает
    CERTIFICATE_VERIFY_FAILED — молча, потому что офлайн здесь не повод падать.
    Отсутствие сводок в телеграме поэтому и не было доказательством молчания
    клиента. Проверку НЕ ослабляем: корни только добавляются к системным.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass                      # certifi нет — остаёмся на системном хранилище
    return ctx


def _post(url: str, fields: dict) -> bool:
    """Отправить. False — сети нет / отказ. Молча: офлайн не повод падать.

    Секрет уходит только внутри URL-пути к Bot API и никогда не печатается.
    """
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "KZ-Health/1"})
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT,
                                    context=_ssl_ctx()) as r:
            return 200 <= getattr(r, "status", 0) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def send(ch: dict, text: str, silent: bool = True) -> bool:
    """Отправить сводку в приватный чат разработчика. Ошибка — просто False.

    `silent=False` — СРОЧНОЕ: телефон должен звякнуть именно сейчас, а не
    потеряться в беззвучной ленте рутинных суточных сводок (BOX-37)."""
    if SEND_HOOK is not None:                          # тестовая заглушка
        return bool(SEND_HOOK(ch, text))
    if ch["transport"] != "telegram" or not ch["token"] or not ch["chat_id"]:
        return False
    url = f"https://api.telegram.org/bot{ch['token']}/sendMessage"
    return _post(url, {"chat_id": ch["chat_id"], "text": text,
                       "disable_notification": "false" if not silent else "true",
                       "disable_web_page_preview": "true"})


# ── сценарии ─────────────────────────────────────────────────────────────────

def collect_and_send(root: Path) -> int:
    """Тело фонового потомка: собрать, просеять, решить (срочно?/буднично?),
    отправить. Всегда 0.

    Порядок гейтов НАМЕРЕННО изменён (BOX-37): раньше суточный предел проверялся
    ДО сбора — и коробка со сломанным доктором просто молчала весь день после
    первой утренней отправки, потому что до payload/phase дело не доходило.
    Теперь сбор идёт всегда (при разрешённой попытке), а решение "будничная
    сводка раз в сутки" или "срочно, прямо сейчас" принимается ПОСЛЕ того, как
    известна реальная фаза. Стоимость этого — доктора могут прогоняться фоном
    (низкий приоритет, клиент не ждёт) до MAX_URGENT_PER_DAY+суточных раз в
    день вместо одного — цена названа явно, не спрятана.

    ЗАМОК СНИМАЕТ ИМЕННО ЭТА ФУНКЦИЯ. Замок берёт tick() перед спавном, а держится
    он всё время сбора — значит и отпускать его должен тот, кто сбор закончил, то
    есть потомок. Снимаем в finally: любой выход (тихий return, исключение,
    заблокированное согласие) обязан освободить следующий тик. Ручной прогон
    `--collect-and-send` тоже снимет замок — это осознанно: человек, запустивший
    сбор руками, и есть сборщик, а протухший замок от чужого мёртвого процесса
    ему мешать не должен."""
    try:
        if blocked_reason(root):                       # согласие отозвали, пока шли
            return 0
        payload, problems = build(root)
        # Реальная проверка только что состоялась (доктора прогнаны) — не важно,
        # спавнил её due() или urgent_check_due(): счётчик "когда в последний раз
        # смотрели на беду" обновляем всегда, иначе он навсегда останется 0 и
        # urgent_check_due() будет считать любую свежую коробку "давно не смотрели".
        mark_urgent_check(root)
        if problems:                                   # рубеж 3 не пропустил
            return 0
        sig = urgent_signature(payload)
        if sig and should_send_urgent(root, sig):
            payload["urgent"] = True
            if send(channel(root), render(payload), silent=False):
                mark_sent(root)
                mark_urgent_sent(root, sig)
            return 0
        # Не срочно (или та же беда уже недавно эскалирована) — обычный суточный
        # гейт: «не чаще раза в сутки» обязано держаться при любом способе
        # запуска, включая ручной, поэтому проверяется и ЗДЕСЬ, а не только в
        # tick().
        if _now() - int(state(root).get("last_sent", 0)) < MIN_OK_HOURS * 3600:
            return 0
        if send(channel(root), render(payload)):
            mark_sent(root)
    except Exception:                                  # noqa: BLE001
        pass                                           # завод уронить нельзя
    finally:
        release_spawn_lock(root)
    return 0


def _spawn_detached(root: Path) -> bool:
    """Отсоединить фоновый сбор. Клиент не ждёт ни секунды и не видит окон."""
    try:
        env = dict(os.environ)
        env["KZ_HEALTH_CHILD"] = "1"
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                  "stdin": subprocess.DEVNULL, "cwd": str(root), "env": env}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
            kwargs["creationflags"] = 0x00000008 | 0x08000000 | 0x00004000
        else:
            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = lambda: os.nice(10)  # noqa: PLW1509
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                          "--collect-and-send", "--root", str(root)], **kwargs)
        return True
    except (OSError, ValueError):
        return False


def tick(root: Path | None = None) -> int:
    """Точка подключения: «если пора — собери (и, может, отправь), иначе выйди».

    Зовётся из агента (`--auto`) и из install/run_doctors.py. НИКОГДА не бросает
    исключений и не тратит заметного времени: чтение пары json и, максимум,
    запуск отсоединённого потомка. Без согласия не делает ничего.

    Два независимых повода запустить фоновый сбор (BOX-37):
      * due(r)             — пора обычной суточной сводке;
      * urgent_check_due(r) — не пора сводке, но не проверяли беду
        URGENT_CHECK_MIN_MINUTES — а сама отправка "срочно" или нет решится
        УЖЕ ВНУТРИ collect_and_send(), после реального сбора фазы.
    Если сработал только второй повод — счётчик суточных попыток (attempts_today,
    он же предохранитель от лавины ручных перезапусков) НЕ трогаем: это не
    попытка отправить сводку, а попытка проверить, не горит ли что-то.

    ЗАМОК (см. acquire_spawn_lock). Всё, что ниже проверки поводов, обязано
    выполниться РОВНО ОДИН РАЗ на коробку, даже если tick() зовут одновременно
    из трёх процессов. Порядок именно такой: сначала выясняем повод (дёшево,
    только чтение), потом берём замок (единственный атомарный шаг), и лишь потом
    трогаем счётчики и спавним. Гонка между чтением повода и замком безвредна:
    повод у всех одинаковый, а пройдёт дальше всё равно один.
    """
    try:
        r = find_root(None) if root is None else root
        if os.environ.get("KZ_HEALTH_CHILD"):          # мы уже внутри сбора
            return 0
        if blocked_reason(r):
            return 0
        daily = due(r)                                 # суточный повод
        if not (daily or urgent_check_due(r)):
            return 0
        if not acquire_spawn_lock(r):
            # Сборщик уже идёт (или только что стартовал в соседнем процессе).
            # Молча уходим: второй сборщик не даст новых данных, а испортит
            # текущие — ровно это и было 17.08.2026.
            return 0
        if daily:
            mark_attempt(r)                            # ДО запуска: без лавины
        mark_urgent_check(r)                           # спавн уже идёт при любом поводе
        if not _spawn_detached(r):
            # Потомок не родился — держать замок не за кем: снимаем сразу, иначе
            # коробка молчала бы до SPAWN_LOCK_STALE из-за чужой неудачи.
            release_spawn_lock(r)
    except Exception:                                  # noqa: BLE001
        pass
    return 0


def auto(root: Path) -> int:
    """`--auto`: тихий тик плюс, при `unknown`, одна строка-подсказка агенту.

    Код возврата всегда 0 — как и у tick(): этот шаг не имеет права мешать
    работе завода ни отправкой, ни ошибкой, ни ненулевым кодом.
    """
    rc = tick(root)
    if os.environ.get("KZ_HEALTH_CHILD"):        # мы внутри фонового сбора
        return rc
    hint = ask_hint(root)
    if hint:
        print(hint)
    return rc


def show(root: Path) -> int:
    """Показать РОВНО то, что уйдёт. Ничего не отправляет — это и есть раскрытость."""
    payload, problems = build(root)
    print("Вот что уходит разработчику раз в сутки — целиком, без сокращений:\n")
    print(render(payload))
    print("\nКуда: " + describe_channel(channel(root)))
    off = blocked_reason(root)
    print("Состояние: " + (f"НИЧЕГО НЕ ОТПРАВЛЯЕТСЯ — {off}" if off
                           else "отправка включена, вы дали согласие"))
    if problems:
        print("\nПроверка на личные данные НЕ пройдена — такой отчёт не отправится:")
        for p in problems[:10]:
            print(f"  ✗ {p}")
    else:
        print("Проверка на личные данные: пройдена (путей, имён и ключей нет).")
    print("\nПолный текст согласия: health_report.py --consent-text")
    print("Отозвать согласие: health_report.py --off")
    return 0


def status(root: Path) -> int:
    st, c = state(root), consent(root)
    human = {"unknown": "не спрашивали", "granted": "дано",
             "denied": "вы отказались", "revoked": "отозвано"}
    _t, ver, _sha = consent_text(root)
    print(f"Согласие на техническую сводку: {human.get(c['state'], c['state'])}"
          + (f" (версия текста {ver})" if c["state"] == "granted" else ""))
    off = blocked_reason(root)
    print("Отправка: " + (f"не идёт — {off}" if off else "включена"))
    print("Куда: " + describe_channel(channel(root)))
    print("Отправлено отчётов: " + str(st.get("seq", 0)))
    if st.get("last_sent"):
        print("Последний отчёт: "
              + time.strftime("%Y-%m-%d %H:%M", time.localtime(int(st["last_sent"]))))
    else:
        print("Последний отчёт: ещё не отправлялся.")
    print("Что именно уходит — покажет: health_report.py --show")
    return 0


def cmd_consent_text(root: Path) -> int:
    text, ver, sha = consent_text(root)
    if not text:
        print(f"Текст согласия не найден ({CONSENT_TEXT_FILE}). "
              f"Пока его нет, ничего никуда не отправляется.")
        return 0
    print(text)
    print(f"\n[версия текста {ver}, отпечаток {sha[:16]}…]")
    return 0


def cmd_consent_state(root: Path) -> int:
    """Машинная сводка для агента: спрашивать ли и что уже записано."""
    c = consent(root)
    _t, ver, sha = consent_text(root)
    print(json.dumps({
        "state": c["state"],
        "need_ask": need_ask(root),
        "asked": int(c.get("asked", 0)),
        "consent_version": ver,
        "consent_text_present": bool(sha),
        "channel_ready": bool(channel(root)["token"] and channel(root)["chat_id"]),
        "blocked_reason": blocked_reason(root),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_consent(root: Path, event: str, how: str) -> int:
    if event == "granted":
        _t, ver, sha = consent_text(root)
        if not sha:
            print(f"Не могу записать согласие: нет файла {CONSENT_TEXT_FILE}. "
                  f"Ничего не отправляется.")
            return 0
        rec = record_consent(root, "granted", how)
        print(f"Спасибо. Согласие записано {rec['ts_local']} "
              f"(версия текста {rec['consent_version']}). Раз в сутки будет уходить "
              f"короткая техническая сводка — посмотреть её можно командой --show.")
        print("Передумаете — скажите «выключи сводку о здоровье», отправка "
              "прекратится сразу и навсегда.")
        tick(root)                                     # первый отчёт — сразу
        return 0
    rec = record_consent(root, event, how)
    if event == "denied":
        print("Понял, ничего отправляться не будет. Это никак не влияет на работу "
              "завода — всё доступно полностью. Больше не спрошу.")
    else:
        print(f"Согласие отозвано {rec['ts_local']}. Отправка прекращена немедленно "
              f"и навсегда. Запись об отзыве сохранена у вас "
              f"({CONSENT_LOG}). Завод работает как обычно.")
    return 0


def cmd_on(root: Path) -> int:
    """«Включить обратно» — это НОВОЕ согласие, а не переключение флага."""
    st = consent_state(root)
    if st == "granted":
        print("Сводка уже включена. Посмотреть, что уходит: --show")
        return 0
    print("Чтобы включить сводку обратно, нужно согласие заново — оно "
          "записывается отдельно, поэтому просто «включить» нельзя.\n")
    cmd_consent_text(root)
    print("\nЕсли согласны — скажите «да, согласна», и я это запишу "
          "(health_report.py --consent-grant --how agent_chat).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Суточная обезличенная сводка о здоровье коробки (по согласию).")
    ap.add_argument("--root", help="корень установки (по умолчанию — авто)")
    ap.add_argument("--how", default="cli", choices=list(CONSENT_METHODS),
                    help="каким действием получено согласие (для записи)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--auto", action="store_true",
                   help="тихо: для агента (не чаще раза в сутки, мгновенный выход)")
    g.add_argument("--show", action="store_true",
                   help="показать, что именно уйдёт (НЕ отправляет)")
    g.add_argument("--status", action="store_true", help="согласие и отправки")
    g.add_argument("--consent-text", action="store_true",
                   help="показать полный текст согласия")
    g.add_argument("--consent-state", action="store_true",
                   help="машинная сводка: спрашивать ли согласие")
    g.add_argument("--consent-asked", action="store_true",
                   help="отметить, что вопрос задан (чтобы не повторять)")
    g.add_argument("--consent-grant", action="store_true", help="согласие дано")
    g.add_argument("--consent-deny", action="store_true", help="клиент отказался")
    g.add_argument("--off", action="store_true",
                   help="отозвать согласие: отправка прекращается навсегда")
    g.add_argument("--on", action="store_true", help="включить обратно (новое согласие)")
    g.add_argument("--collect-and-send", action="store_true",
                   help=argparse.SUPPRESS)          # служебное: фоновый потомок
    a = ap.parse_args(argv)

    root = find_root(a.root)
    if a.consent_text:
        return cmd_consent_text(root)
    if a.consent_state:
        return cmd_consent_state(root)
    if a.consent_asked:
        mark_asked(root)
        return 0
    if a.consent_grant:
        return cmd_consent(root, "granted", a.how)
    if a.consent_deny:
        return cmd_consent(root, "denied", a.how)
    if a.off:
        return cmd_consent(root, "revoked", a.how)
    if a.on:
        return cmd_on(root)
    if a.status:
        return status(root)
    if a.show:
        return show(root)
    if getattr(a, "collect_and_send", False):
        return collect_and_send(root)
    return auto(root)                                 # --auto и запуск без флагов


if __name__ == "__main__":
    sys.exit(main())
