# -*- coding: utf-8 -*-
"""
github_sync — завод клиента живёт в ЕГО личном приватном репозитории GitHub.

ЗАЧЕМ. Сегодня завод существует ровно в одном экземпляре — на компьютере
клиента. Умер диск, снесли папку, украли ноутбук — пропали и код, и смыслы, и
контент-план: резервной копии нет никакой. Плюс человек привязан к десктопу, а
работать со смыслами и текстами хочется с телефона.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ — ПРОЧЕСТЬ ПЕРВЫМ. Телефон НЕ становится заводом.
В облачной песочнице нет сырья клиента (оно на его Google Диске), нет модели
распознавания речи (~2,7 ГБ), нет ffmpeg, шрифтов и банка музыки, и нет ключей
(`.env` не уезжает НИКОГДА). Поэтому с телефона реальны смыслы, тексты,
контент-план, сценарии, разбор и правка кода; рендер, нарезка эфиров и
публикация остаются на десктопе. Обещать «завод в кармане» нельзя — это ровно
тот класс «отказ выглядит как успех», который стоил проекту недели.

БЕЗ GIT, СОЗНАТЕЛЬНО. `install/stages.json` говорит прямым текстом: на чистой
Windows git не стоит никогда и коробке не нужен. Ставить его трём живым
клиентам ради резервной копии — новый обязательный ручной шаг, то есть BOX-73
второй раз. Поэтому здесь GitHub REST API поверх `urllib`: коробка не знает,
что такое git, а коммиты собираются через Git Data API.

КАК КЛИЕНТ ЛОГИНИТСЯ. Device Flow — тот же способ, которым логинятся телевизоры:
коробка печатает восьмизначный код и ссылку, человек открывает её на любом
устройстве, входит в свой GitHub и подтверждает. Токен коробка забирает сама и
кладёт в `.env` (0600). Ни копипасты токенов, ни настроек.

ЧТО УЕЗЖАЕТ. Белый список (см. `_ALLOWED_EXT` / `_INCLUDE_DIRS`): код, пресеты,
настройки, смыслы, документы. Медиа и бинарники исключены целиком — репозиторий
это смыслы и код, а не архив материалов. Восстановление: поставить коробку из
установщика (он у клиента есть) и подложить репозиторий; сырьё и так лежит на
Google Диске.

ЧТО НЕ УЕЗЖАЕТ НИКОГДА (`_DENY_*`, чёрный список сильнее белого):
    .env · license.key · *.key/*.pem · .venv/ · python/ · .state/ ·
    .update_backup/ · out_*/ · медиа-папки · installer/vendor/ · любые бинарники

ГЕЙТ СЕКРЕТОВ. Перед КАЖДОЙ отправкой содержимое проверяется на значения из
живого `.env` и на известные формы ключей. Твёрдая находка — отправка
отменяется ЦЕЛИКОМ и файл называется вслух: молча вырезать нельзя, клиент
обязан узнать, что у него ключ лежал в коде. Плюс репозиторий создаётся
приватным, и приватность перепроверяется перед каждой отправкой.

ЗАПУСК:
    python github_backup.py --login          # войти в свой GitHub (Device Flow)
    python github_backup.py --setup          # завести приватный репозиторий
    python github_backup.py --push           # отправить изменения
    python github_backup.py --auto           # тихо: для хука SessionStart
    python github_backup.py --status         # что настроено (без сети)
    python github_backup.py --pull           # что изменилось в репозитории
    python github_backup.py --pull --apply   # применить эти изменения локально
    python github_backup.py --list           # что именно уедет, и что отсеяно

Коды возврата: 0 — успех ИЛИ нечего делать ИЛИ нет сети (не повод падать);
1 — сделать не удалось и человек должен знать; 2 — плохой ввод.
Только stdlib. Без input(). Секретов не печатает.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent

# ── Постоянные ────────────────────────────────────────────────────────────────

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"
NET_TIMEOUT = 30
USER_AGENT = "kontent-zavod-github-sync/1"

#: OAuth App владельца. НЕ СЕКРЕТ: client_id device-flow публичен по устройству
#: протокола (секрета у такого приложения нет вовсе — потому оно и годится для
#: раздачи в коробке). Заводится один раз: github.com → Settings → Developer
#: settings → OAuth Apps → New, галка «Enable Device Flow».
#: ⚠️ Пока строка пуста, --login честно говорит об этом и предлагает запасной
#: путь (--token), а не притворяется сломанной сетью.
#: Заведено 04.09.2026 под аккаунтом владельца (aevkone), приложение
#: «Контент-завод», галка Enable Device Flow подтверждена после сохранения.
#: Приложение принадлежит ОСНОВНОМУ аккаунту специально: удали его вместе с
#: временным аккаунтом — и вход отвалится у всех клиентов разом.
OAUTH_CLIENT_ID = "Ov23liD1n0u1aJWJwHsH"

#: Права, которые просим. `repo` — минимум, которым можно завести ПРИВАТНЫЙ
#: репозиторий и писать в него. Меньше нельзя: `public_repo` приватные не умеет,
#: а приватность здесь обязательна — в репозитории лежит код завода.
OAUTH_SCOPE = "repo"

CONFIG_FILE = "config/github.json"
ENV_FILE = ".env"
TOKEN_KEY = "GITHUB_TOKEN"
BASELINE_FILE = "config/box_files.json"     # слепок того, что доставила коробка
BACKUP_DIR = ".update_backup"
FROM_GITHUB_DIR = "from-github"             # куда откладываем локальные версии

DEFAULT_REPO_NAME = "kontent-zavod"
DEFAULT_BRANCH = "main"

#: Сколько изменившихся файлов `--auto` берёт на себя. Каждый блоб — отдельное
#: обращение к GitHub, а хук SessionStart ограничен 120 секундами: первая
#: отправка на сотни файлов в него не влезет и оборвётся на середине.
#: Больше потолка — `--auto` НЕ пытается и говорит человеку одну команду.
#: Оборванная отправка выглядела бы как сделанная копия, а это ровно тот класс
#: отказов, который проект ловит всю неделю.
AUTO_MAX_CHANGED = 50

#: Как часто `--auto` напоминает про ненастроенную копию. Каждый запуск помощника
#: — это нытьё, которое человек научится пролистывать за два дня; раз в сутки
#: сообщение остаётся заметным. Молчать нельзя вовсе (урок BOX-73).
NAG_INTERVAL_SEC = 24 * 3600

#: Потолки. Репозиторий кода не должен содержать файлов на мегабайты — если
#: такой нашёлся, это почти наверняка медиа или архив, просочившийся мимо
#: чёрного списка. Отказываем вслух, а не отправляем гигабайт молча.
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024

# ── Белый список: ЧТО уезжает ────────────────────────────────────────────────

#: Расширения, которые вообще могут уехать. Список сильнее каталогов ниже:
#: положил кто-нибудь .mp4 в modules/ — он всё равно не поедет.
_ALLOWED_EXT = {
    ".py", ".md", ".json", ".txt", ".yml", ".yaml", ".toml",
    ".cfg", ".ini", ".csv", ".sh", ".ps1", ".bat", ".cmd", ".sql", ".srt", ".vtt",
}
#: Файлы без расширения (или с чужим), которые всё же везём — поимённо.
_ALLOWED_NAMES = {"LICENSE", "NOTICE", ".gitignore", ".gitattributes"}

#: Каталоги верхнего уровня, внутрь которых заходим. Всё остальное не смотрим
#: вовсе — так дерево обходится быстро и случайная папка клиента не уедет.
_INCLUDE_DIRS = (
    "modules", "skills", "install", "installer", "styles", "config",
    "docs", "ученикам", "content_plan", "script_writer", "reminders",
    "fonts",            # только .json-описания, сами шрифты отсеются расширением
    "prompts", "templates",
)

# ── Чёрный список: ЧТО не уезжает НИКОГДА (сильнее белого) ───────────────────

#: ПРАВКА КЛИЕНТСКОЙ КОПИИ 09.09.2026 (НиколаевскТВ) — перенести в мастер-копию.
#: `health_channel.json` и `config/update_channel.json` вложены в коробку САМИМ
#: разработчиком и содержат ЕГО живые ключи (бот сводки о здоровье и токен
#: канала обновлений). Гейт секретов ловит их и отменяет отправку целиком —
#: то есть коробка в поставке не может сделать копию самой себя. В репозиторий
#: клиента эти ключи класть нельзя в принципе, а для восстановления они не
#: нужны: оба файла приезжают заново с установщиком (см. шапку, «Восстановление»).
_DENY_NAMES = {"license.key", "id_rsa", "id_ed25519", ".npmrc", ".netrc",
               "health_channel.json", "update_channel.json"}
_DENY_SUFFIX = (".key", ".pem", ".pfx", ".p12", ".keystore", ".jks")
#: Сегменты пути. Проверяются как ОТДЕЛЬНЫЕ части, а не подстроки: каталог
#: `vendor` ловит `installer/vendor/`, но не файл `vendored_notes.md`.
_DENY_SEGMENTS = {
    ".venv", "venv", "python", "__pycache__", ".state", ".git", ".idea",
    ".update_backup", "node_modules", "vendor", "wheelhouse", ".pytest_cache",
    ".cache_models", ".cache_src", "dist", "build", "site-packages",
}
#: Префиксы пути (POSIX, от корня установки).
_DENY_PREFIXES = (
    "out_", "output/", "inbox/", "photos/", "video/", "talking/",
    "music/", "music_cache/", "music_preview/", "stock/", "primer/",
    ".cache", "sync/", "tests_guard/", "net_fix/",
)
#: Точные пути. `config/github.json` — наш собственный журнал отправок: он
#: меняется КАЖДОЙ отправкой, и если его возить, каждая отправка порождала бы
#: следующую. `install/.state.json` и `install/.answers.json` — рантайм-файлы
#: установки, там могут лежать ответы клиента (та же грабля, что разобрана в
#: `updater._FORBIDDEN`: они лежат РЯДОМ с кодом, и правило «.state/» их не ловит).
_DENY_PATHS = {
    CONFIG_FILE, "install/.state.json", "install/.answers.json",
}

# ── Гейт секретов ────────────────────────────────────────────────────────────

#: Известные формы ключей. Находка = ТВЁРДАЯ: отправка отменяется целиком.
_HARD_SECRET_PATTERNS = (
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "ключ Anthropic"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "токен GitHub"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"), "токен GitHub (fine-grained)"),
    (re.compile(r"\bgho_[A-Za-z0-9]{30,}"), "токен GitHub (OAuth)"),
    (re.compile(r"vk1\.a\.[A-Za-z0-9_\-]{50,}"), "токен ВКонтакте"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "ключ Google"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "токен Slack"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
    (re.compile(r"\b\d{6,10}:AA[A-Za-z0-9_\-]{30,}"), "токен бота Telegram"),
)
#: Общий шаблон. Находка = ПОДОЗРЕНИЕ: файл исключается из отправки, остальное
#: едет. Отказывать всей копии по совпадению «слово token и длинная строка»
#: нельзя — так резервная копия перестанет делаться вовсе, а это хуже.
_SOFT_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|secret|api[_-]?key|apikey|password|passwd|pwd)\b\s*[=:]\s*"
    r"[\"']?([A-Za-z0-9_\-]{32,})[\"']?"
)
#: Значения из .env короче этого не ищем: «1», «yes», «true» встретятся в любом
#: файле и завалят отправку ложными находками.
_MIN_ENV_VALUE_LEN = 12

#: Отметка «это образец, а не ключ». Ставится В ТОЙ ЖЕ СТРОКЕ.
#: ⚠️ Нужна не для удобства. Найдено живым прогоном по дереву 02.09.2026: в
#: `test_mac_installer_readiness.py` лежит хрестоматийный ПРИМЕР токена бота из
#: документации Telegram — фикстура, не ключ. Без исключения одна такая строка
#: останавливала бы резервную копию НАВСЕГДА, и человек, у которого копия не
#: делается неделю, в конце концов просто выключил бы весь механизм. Отметка
#: живёт в самом файле (видна при чтении кода, а не спрятана в настройках), и
#: `--list` называет число исключённых строк вслух — тихого исключения нет.
SAMPLE_MARK = "github_sync: образец"


# ── Корень, конфиг, .env ─────────────────────────────────────────────────────


def root() -> Path:
    """Корень установки. FACTORY_ROOT уважается — на нём стоят тесты."""
    env_root = os.environ.get("FACTORY_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root).resolve()
    return HERE


def load_config(rt: Optional[Path] = None) -> dict:
    """Настройки резервной копии. Нет файла — пустой словарь, не отказ."""
    rt = rt or root()
    try:
        data = json.loads((rt / CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict, rt: Optional[Path] = None) -> None:
    rt = rt or root()
    path = rt / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _parse_env(text: str) -> dict:
    """Разбор .env. Свой, а не из factory_config: гейту секретов нужны ЗНАЧЕНИЯ,
    а factory_config их сознательно наружу не отдаёт."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            out[key.strip()] = value
    return out


def env_values(rt: Optional[Path] = None) -> dict:
    rt = rt or root()
    try:
        return _parse_env((rt / ENV_FILE).read_text(encoding="utf-8"))
    except OSError:
        return {}


def get_token(rt: Optional[Path] = None) -> Optional[str]:
    """Токен: сначала окружение (так его подаёт тест), потом .env."""
    from_env = os.environ.get(TOKEN_KEY)
    if from_env:
        return from_env.strip()
    return env_values(rt).get(TOKEN_KEY)


def save_token(token: str, rt: Optional[Path] = None) -> None:
    """Кладём токен в .env, права 0600. Существующую строку заменяем, чужие не
    трогаем — в .env лежат все остальные ключи клиента."""
    rt = rt or root()
    path = rt / ENV_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(TOKEN_KEY + "="):
            lines[i] = f"{TOKEN_KEY}={token}"
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Личный доступ к GitHub — резервная копия завода (github_backup.py).")
        lines.append(f"{TOKEN_KEY}={token}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)   # 0600
    except OSError:
        pass          # на Windows права другие; отсутствие chmod не повод падать


# ── Отбор файлов ─────────────────────────────────────────────────────────────


def is_denied(rel: str) -> Optional[str]:
    """Причина отказа для пути (POSIX, от корня установки), либо None.

    Чёрный список проверяется ПЕРВЫМ и сильнее белого: файл, попавший под любое
    правило здесь, не уедет, даже если лежит в разрешённом каталоге с
    разрешённым расширением.
    """
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return "подозрительный путь"
    if re.match(r"^[A-Za-z]:", rel):
        return "подозрительный путь"
    if rel in _DENY_PATHS:
        return "служебный файл коробки"
    parts = rel.split("/")
    name = parts[-1]
    if name == ENV_FILE or name.startswith(".env"):
        return "файл секретов"
    if name.lower() in _DENY_NAMES:
        return "личный ключ"
    if any(name.lower().endswith(s) for s in _DENY_SUFFIX):
        return "файл ключа"
    for seg in parts:
        if seg in _DENY_SEGMENTS:
            return f"служебный каталог «{seg}»"
    for pref in _DENY_PREFIXES:
        if rel.startswith(pref):
            return f"каталог результатов или материалов «{pref}»"
    return None


def is_included(rel: str) -> bool:
    """Попадает ли путь в белый список (чёрный проверяется отдельно и раньше)."""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    name = parts[-1]
    if name not in _ALLOWED_NAMES and Path(name).suffix.lower() not in _ALLOWED_EXT:
        return False
    if len(parts) == 1:
        return True                      # файл в корне установки
    return parts[0] in _INCLUDE_DIRS


def blob_sha(data: bytes) -> str:
    """sha1 блоба git — тот же номер, что показывает GitHub.

    Считается ЛОКАЛЬНО, и в этом весь смысл: сравнив его с деревом на сервере,
    отправляем только изменившееся, а не всё дерево каждый раз.
    """
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def collect(rt: Optional[Path] = None) -> tuple:
    """Обход дерева. Возвращает ({путь: содержимое}, [(путь, причина отказа)]).

    Каталоги из чёрного списка не открываются вовсе — иначе обход .venv и
    моделей занял бы минуты на ровном месте.
    """
    rt = rt or root()
    files: dict = {}
    skipped: list = []
    total = 0

    def take(entry: Path, rel: str) -> None:
        nonlocal total
        reason = is_denied(rel)
        if reason:
            skipped.append((rel, reason))
            return
        if not is_included(rel):
            return
        try:
            if entry.stat().st_size > MAX_FILE_BYTES:
                skipped.append((rel, f"крупнее {MAX_FILE_BYTES // 1024 // 1024} МБ"))
                return
            data = entry.read_bytes()
        except OSError as exc:
            skipped.append((rel, f"не прочитан: {exc.__class__.__name__}"))
            return
        total += len(data)
        files[rel] = data

    def walk(base: Path, prefix: str) -> None:
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            rel = prefix + entry.name
            if entry.is_dir():
                if entry.name in _DENY_SEGMENTS or is_denied(rel + "/x"):
                    continue
                walk(entry, rel + "/")
            elif entry.is_file():
                take(entry, rel)

    if not rt.is_dir():
        return files, skipped
    for entry in sorted(rt.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            if entry.name not in _INCLUDE_DIRS or entry.name in _DENY_SEGMENTS:
                continue
            walk(entry, entry.name + "/")
        elif entry.is_file():
            take(entry, entry.name)

    if total > MAX_TOTAL_BYTES:
        raise ValueError(
            f"суммарный объём {total // 1024 // 1024} МБ больше потолка "
            f"{MAX_TOTAL_BYTES // 1024 // 1024} МБ — так резервная копия не делается; "
            "посмотри `--list`, что именно разрослось")
    return files, skipped


# ── Гейт секретов ────────────────────────────────────────────────────────────


def scan_secrets(files: dict, secrets: dict) -> tuple:
    """(твёрдые находки, подозрения, исключённые образцы).

    Каждая находка — (путь, номер строки, что нашли).

    Твёрдая находка отменяет отправку ЦЕЛИКОМ: клиент обязан узнать, что у него
    ключ лежит в коде, а не получить тихо урезанную копию. Подозрение исключает
    один файл — иначе одно совпадение общего шаблона убило бы резервную копию
    целиком, а это хуже, чем не отправить один файл.

    Строка с отметкой `SAMPLE_MARK` не проверяется вовсе, но попадает третьим
    списком: исключений может быть сколько угодно, а невидимых — ни одного.
    ⚠️ Живое значение из .env отметкой НЕ прощается: если ключ, которым завод
    прямо сейчас работает, лежит в коде, это беда независимо от комментария
    рядом с ним.
    """
    hard: list = []
    soft: list = []
    samples: list = []
    values = {v for k, v in secrets.items()
              if len(v) >= _MIN_ENV_VALUE_LEN and k != TOKEN_KEY}
    # GITHUB_TOKEN исключён отдельно: он лежит только в .env, который не уезжает
    # по чёрному списку, а держать его в наборе значило бы искать свой же токен.
    for rel, data in sorted(files.items()):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue                      # не текст — шаблонами не проверяется
        for num, line in enumerate(text.splitlines(), 1):
            живое = False
            for value in values:
                if value in line:
                    hard.append((rel, num, "значение ключа из .env"))
                    живое = True
                    break
            if живое:
                continue
            if SAMPLE_MARK in line:
                if any(p.search(line) for p, _ in _HARD_SECRET_PATTERNS) \
                        or _SOFT_SECRET_PATTERN.search(line):
                    samples.append((rel, num, "отмечено как образец"))
                continue
            for pattern, what in _HARD_SECRET_PATTERNS:
                if pattern.search(line):
                    hard.append((rel, num, what))
            match = _SOFT_SECRET_PATTERN.search(line)
            if match:
                soft.append((rel, num, f"похоже на «{match.group(1)}»"))
    hard_files = {item[0] for item in hard}
    soft = [item for item in soft if item[0] not in hard_files]
    return hard, soft, samples


# ── GitHub API ───────────────────────────────────────────────────────────────


class GitHubError(RuntimeError):
    """Ошибка обращения к GitHub. Текст пригоден для показа человеку."""


def _request(url: str, token: Optional[str] = None, data: Optional[dict] = None,
             method: Optional[str] = None, form: bool = False) -> tuple:
    """Один запрос. Возвращает (код, разобранный json | None).

    Сетевую беду превращаем в GitHubError с узнаваемым началом текста: выше по
    стеку `--auto` отличает её от настоящего отказа и молчит — отсутствие
    интернета не повод ломать рабочий день клиента.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json" if form else "application/vnd.github+json",
    }
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method or ("POST" if body else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return resp.status, None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return exc.code, None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubError(f"сеть недоступна ({exc.__class__.__name__})") from exc


def api(path: str, token: str, data: Optional[dict] = None,
        method: Optional[str] = None):
    """Обращение к api.github.com с разбором отказов в человеческий текст."""
    status, payload = _request(API + path, token=token, data=data, method=method)
    if 200 <= status < 300:
        return payload
    message = str(payload.get("message", "")) if isinstance(payload, dict) else ""
    if status == 401:
        raise GitHubError("GitHub не принял доступ — войди заново: --login")
    if status == 403 and "rate limit" in message.lower():
        raise GitHubError("GitHub временно ограничил число обращений, попробуй позже")
    raise GitHubError(f"GitHub ответил {status}: {message or 'без пояснения'}")


# ── Вход по Device Flow ──────────────────────────────────────────────────────


def login_device(client_id: str = "", quiet: bool = False) -> str:
    """Вход в GitHub кодом устройства. Возвращает токен.

    Человек не вводит в коробку ничего: он открывает ссылку на ЛЮБОМ устройстве
    (хоть на телефоне), входит в свой GitHub и вбивает восемь символов.
    """
    client_id = client_id or OAUTH_CLIENT_ID
    if not client_id:
        raise GitHubError(
            "вход одной кнопкой пока не подключён: владелец ещё не завёл OAuth App "
            "и не прописал OAUTH_CLIENT_ID. Запасной путь работает уже сейчас — "
            "github.com → Settings → Developer settings → Personal access tokens → "
            "Fine-grained, права Contents: Read and write, потом "
            "`python github_backup.py --token <вставить>`")
    status, payload = _request(DEVICE_CODE_URL, form=True,
                               data={"client_id": client_id, "scope": OAUTH_SCOPE})
    if status != 200 or not isinstance(payload, dict) or "device_code" not in payload:
        raise GitHubError("GitHub не выдал код устройства — попробуй позже")
    device_code = payload["device_code"]
    interval = int(payload.get("interval", 5))
    expires = time.time() + int(payload.get("expires_in", 900))
    if not quiet:
        print()
        print("  ОТКРОЙ НА ЛЮБОМ УСТРОЙСТВЕ:  " + str(payload.get(
            "verification_uri", "https://github.com/login/device")))
        print("  И ВВЕДИ КОД:                  " + str(payload.get("user_code", "")))
        print()
        print("  Войдёшь в свой GitHub, нажмёшь «Authorize» — и всё, я жду тут.")
        print()
    while time.time() < expires:
        time.sleep(interval)
        status, payload = _request(
            DEVICE_TOKEN_URL, form=True,
            data={"client_id": client_id, "device_code": device_code,
                  "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if not isinstance(payload, dict):
            continue
        if payload.get("access_token"):
            return str(payload["access_token"])
        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = int(payload.get("interval", interval + 5))
            continue
        if error == "expired_token":
            raise GitHubError("код устарел — запусти --login ещё раз")
        if error == "access_denied":
            raise GitHubError("доступ не подтверждён в браузере — запусти --login ещё раз")
        raise GitHubError(f"GitHub отказал: {error or 'без пояснения'}")
    raise GitHubError("время ожидания вышло — запусти --login ещё раз")


# ── Репозиторий ──────────────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def ensure_repo(token: str, name: str = DEFAULT_REPO_NAME,
                rt: Optional[Path] = None) -> dict:
    """Найти или завести ПРИВАТНЫЙ репозиторий. Идемпотентно.

    Приватность здесь не пожелание: в репозитории лежит код завода. Публичный
    отдал бы его кому угодно — ровно то, из-за чего и канал обновлений приватный
    (см. `updater.DEFAULT_REPO`).
    """
    me = api("/user", token)
    owner = str(me.get("login", "")) if isinstance(me, dict) else ""
    if not owner:
        raise GitHubError("GitHub не назвал имя аккаунта")
    status, payload = _request(f"{API}/repos/{owner}/{name}", token=token)
    if status == 200 and isinstance(payload, dict):
        if not payload.get("private"):
            raise GitHubError(
                f"репозиторий {owner}/{name} ПУБЛИЧНЫЙ — в нём лежал бы код завода "
                "на виду у всех. Сделай его приватным (Settings → General → "
                "Change visibility) и запусти снова")
    elif status == 404:
        api("/user/repos", token, data={
            "name": name,
            "private": True,
            "description": "Контент-завод: код, смыслы и настройки. Приватный.",
            "auto_init": False,
            "has_issues": False, "has_wiki": False, "has_projects": False,
        })
    else:
        raise GitHubError(f"GitHub ответил {status} на проверку репозитория")
    cfg = load_config(rt)
    cfg.update({"owner": owner, "repo": name,
                "branch": cfg.get("branch") or DEFAULT_BRANCH,
                "created_at": cfg.get("created_at") or _now()})
    save_config(cfg, rt)
    return {"owner": owner, "repo": name, "private": True}


def remote_tree(token: str, owner: str, repo: str, branch: str) -> tuple:
    """(sha последнего коммита, {путь: sha блоба}). Пустой репозиторий → (None, {})."""
    status, payload = _request(f"{API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
                               token=token)
    if status in (404, 409):
        return None, {}                   # ветки ещё нет — это первая отправка
    if status != 200 or not isinstance(payload, dict):
        raise GitHubError(f"GitHub ответил {status} на чтение ветки {branch}")
    commit_sha = (payload.get("object") or {}).get("sha")
    if not commit_sha:
        return None, {}
    tree = api(f"/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1", token)
    files = {}
    if isinstance(tree, dict):
        if tree.get("truncated"):
            # Дерево обрезано сервером: сравнение было бы неполным, и мы отчитались
            # бы «изменений нет» при изменившихся файлах. Молчать тут нельзя.
            raise GitHubError(
                "дерево репозитория слишком велико и GitHub отдал его не целиком — "
                "сравнение было бы неверным; напиши разработчику")
        for item in tree.get("tree", []):
            if item.get("type") == "blob":
                files[item["path"]] = item["sha"]
    return commit_sha, files


def should_nag(rt: Optional[Path] = None, now: Optional[float] = None) -> bool:
    """Пора ли напомнить про ненастроенную копию (и отметить, что напомнили).

    Отметка живёт в `config/github.json` — там же, где остальное состояние.
    Файл ещё не заведён (обычный случай для ненастроенной копии) — заводим:
    одна строка про напоминание безопаснее, чем нытьё при каждом запуске.
    """
    rt = rt or root()
    now = now if now is not None else time.time()
    cfg = load_config(rt)
    last = cfg.get("nagged_at", 0)
    try:
        last = float(last)
    except (TypeError, ValueError):
        last = 0.0
    if now - last < NAG_INTERVAL_SEC:
        return False
    cfg["nagged_at"] = now
    try:
        save_config(cfg, rt)
    except OSError:
        pass          # некуда записать — лучше напомнить лишний раз, чем молчать
    return True


def push(token: str, rt: Optional[Path] = None, quiet: bool = False,
         message: str = "", max_changed: Optional[int] = None) -> dict:
    """Отправка изменений. Возвращает сводку.

    Порядок важен: сначала СОБРАЛИ, потом ПРОВЕРИЛИ на секреты, и только потом
    хоть что-то ушло в сеть. Иначе твёрдая находка обнаружилась бы после того,
    как половина файлов уже уехала — а уехавший ключ обратно не забрать.
    """
    rt = rt or root()
    cfg = load_config(rt)
    owner, repo = cfg.get("owner"), cfg.get("repo")
    branch = cfg.get("branch") or DEFAULT_BRANCH
    if not owner or not repo:
        raise GitHubError("репозиторий ещё не заведён — запусти --setup")

    files, skipped = collect(rt)
    if not files:
        raise GitHubError("не нашлось ни одного файла для отправки — проверь --list")

    hard, soft, samples = scan_secrets(files, env_values(rt))
    if samples and not quiet:
        print(f"  ℹ️ строк, отмеченных как образец и потому пропущенных: {len(samples)}")
    if hard:
        lines = [f"  {rel}:{num} — {what}" for rel, num, what in hard[:20]]
        raise GitHubError(
            "ОТПРАВКА ОТМЕНЕНА: в файлах, которые собрались уехать, лежат секреты.\n"
            + "\n".join(lines)
            + ("\n  …и другие" if len(hard) > 20 else "")
            + "\n  Убери их в .env и запусти снова. Ключ, попавший в репозиторий, —"
              " это ключ, который уже пора менять.")
    # ⚠️ Про исключённый файл говорим ВСЕГДА, в том числе в тихом режиме `--auto`.
    # Тихое исключение — это копия, в которой чего-то нет, и никто об этом не
    # знает: человек рассчитывает на резервную копию файла, которого там не
    # лежит. Молчаливость `--auto` существует ради «сказать нечего», а здесь
    # есть что.
    for rel, num, what in soft:
        print(f"  ⚠️ {rel}:{num} — {what}; файл НЕ отправлен")
        files.pop(rel, None)

    # Приватность перепроверяем перед КАЖДОЙ отправкой: репозиторий могли
    # переключить в публичный уже после создания, и тогда очередная отправка
    # выложила бы код завода наружу.
    info = api(f"/repos/{owner}/{repo}", token)
    if isinstance(info, dict) and not info.get("private"):
        raise GitHubError(
            f"репозиторий {owner}/{repo} стал ПУБЛИЧНЫМ — отправка отменена. "
            "Верни ему приватность в настройках репозитория")

    parent_sha, remote = remote_tree(token, owner, repo, branch)

    # 🔴 ПРАВКИ С ТЕЛЕФОНА НЕ ПЕРЕТИРАЕМ. Найдено замером 03.09.2026, до того как
    # это дошло до клиента. Отправка считает «изменившимся» всё, что на диске
    # отличается от репозитория, — и ей всё равно, КТО отличается. Человек правит
    # контент-план с телефона, потом садится за компьютер, хук отправляет копию —
    # и старая местная версия молча ложится поверх телефонной. Правка исчезает,
    # никто ничего не заметил.
    # Отличить одно от другого можно точно: мы помним номер СВОЕГО последнего
    # коммита. Голова репозитория ушла от него — значит писал кто-то ещё (телефон),
    # и отправлять поверх нельзя. Молчать тоже нельзя: человек рассчитывает, что
    # копия свежая.
    было = (cfg.get("last_push") or {}).get("sha")
    if parent_sha and было and parent_sha != было:
        return {"changed": 0, "removed": 0, "total": len(files),
                "skipped": len(skipped), "commit": None, "excluded": len(soft),
                "too_many": 0, "чужие_правки": True}

    local = {rel: blob_sha(data) for rel, data in files.items()}
    changed = sorted(rel for rel, sha in local.items() if remote.get(rel) != sha)
    removed = sorted(rel for rel in remote if rel not in local)
    if not changed and not removed:
        return {"changed": 0, "removed": 0, "total": len(local),
                "skipped": len(skipped), "commit": None, "excluded": len(soft),
                "too_many": 0, "чужие_правки": False}

    if max_changed is not None and len(changed) > max_changed:
        # Ничего не отправляем ВОВСЕ: половина уехавших блобов без коммита —
        # это не «частично сделано», это мусор на сервере и ложное ощущение,
        # что копия есть.
        return {"changed": 0, "removed": 0, "total": len(local),
                "skipped": len(skipped), "commit": None, "excluded": len(soft),
                "too_many": len(changed), "чужие_правки": False}

    for rel in changed:
        made = api(f"/repos/{owner}/{repo}/git/blobs", token, data={
            "content": base64.b64encode(files[rel]).decode("ascii"),
            "encoding": "base64",
        })
        if not isinstance(made, dict) or made.get("sha") != local[rel]:
            # Номер блоба мы считаем сами; расхождение значит, что мы неверно
            # поняли формат, и молча продолжать нельзя — дерево вышло бы кривым.
            raise GitHubError(f"GitHub принял {rel} с другим номером — отправка прервана")

    # Дерево отдаём ЦЕЛИКОМ и БЕЗ base_tree: тогда удалённый локально файл
    # исчезает и в репозитории. С base_tree он остался бы там навсегда.
    tree_items = [{"path": rel, "mode": "100644", "type": "blob", "sha": sha}
                  for rel, sha in sorted(local.items())]
    tree = api(f"/repos/{owner}/{repo}/git/trees", token, data={"tree": tree_items})
    if not isinstance(tree, dict) or not tree.get("sha"):
        raise GitHubError("GitHub не собрал дерево файлов")

    text = message or (f"завод: изменено {len(changed)}"
                       + (f", удалено {len(removed)}" if removed else ""))
    commit = api(f"/repos/{owner}/{repo}/git/commits", token, data={
        "message": f"{text}\n\nОтправлено заводом {_now()}.",
        "tree": tree["sha"],
        "parents": [parent_sha] if parent_sha else [],
    })
    if not isinstance(commit, dict) or not commit.get("sha"):
        raise GitHubError("GitHub не создал запись коммита")

    if parent_sha:
        api(f"/repos/{owner}/{repo}/git/refs/heads/{branch}", token,
            data={"sha": commit["sha"], "force": False}, method="PATCH")
    else:
        api(f"/repos/{owner}/{repo}/git/refs", token,
            data={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})

    cfg["last_push"] = {"sha": commit["sha"], "at": _now(),
                        "files": len(local), "changed": len(changed)}
    save_config(cfg, rt)
    return {"changed": len(changed), "removed": len(removed), "total": len(local),
            "skipped": len(skipped), "commit": commit["sha"], "excluded": len(soft),
            "too_many": 0, "чужие_правки": False}


# ── Работа с GitHub БЕЗ gh и БЕЗ git ─────────────────────────────────────────
#
# Зачем это здесь. Пульт маркетолога сначала звал `gh repo create --template` и
# `git clone`, то есть требовал поставить ДВЕ программы и пройти отдельный вход
# `gh auth login` с выбором трёх пунктов меню. Для человека, который никогда не
# настраивал ПК, это и есть главная стена: до первого клиента он не доходит.
#
# Всё то же самое делается теми же запросами, что уже работают в этом файле:
# репозиторий из шаблона — `POST /repos/{шаблон}/generate`, выкладка файлов на
# диск — то же дерево и те же блобы, что читает `pull`. Ни gh, ни git не нужны,
# а вход остаётся один — тот, что уже есть (код устройства).


def generate_from_template(token: str, template: str, name: str,
                           private: bool = True) -> dict:
    """Завести репозиторий ИЗ ШАБЛОНА. Замена `gh repo create --template`.

    Идемпотентно: репозиторий с таким именем уже есть — возвращаем его, а не
    падаем. Человек, повторивший команду, не должен получать отказ.
    """
    me = api("/user", token)
    owner = str(me.get("login", "")) if isinstance(me, dict) else ""
    if not owner:
        raise GitHubError("GitHub не назвал имя аккаунта")

    status, payload = _request(f"{API}/repos/{owner}/{name}", token=token)
    if status == 200 and isinstance(payload, dict):
        return {"owner": owner, "repo": name, "created": False,
                "private": bool(payload.get("private"))}

    if "/" not in template:
        raise GitHubError(
            f"шаблон «{template}» задан неправильно — нужно «владелец/репозиторий»")
    t_owner, t_repo = template.split("/", 1)
    payload = api(f"/repos/{t_owner}/{t_repo}/generate", token, data={
        "owner": owner, "name": name, "private": private,
        "description": "Контент-завод клиента. Приватный.",
    })
    if not isinstance(payload, dict) or not payload.get("name"):
        raise GitHubError("GitHub не завёл репозиторий из шаблона")
    return {"owner": owner, "repo": name, "created": True, "private": private}


def materialize(token: str, owner: str, repo: str, dest: Path,
                branch: str = DEFAULT_BRANCH, prefix: str = "",
                quiet: bool = True) -> int:
    """Выложить содержимое репозитория на диск. Замена `git clone` и `git pull`.

    `prefix` ограничивает выкладку одним поддеревом — так обновление движка
    трогает только `engine/` и физически не может задеть `data/`. Это то же
    свойство, что давал `git checkout upstream/main -- engine`, но без git.

    Возвращает число записанных файлов. Файл, совпавший по отпечатку, не
    переписывается: лишняя запись меняла бы время файла и путала человека.
    """
    _sha, remote = remote_tree(token, owner, repo, branch)
    if not remote:
        return 0
    записано = 0
    for rel, sha in sorted(remote.items()):
        if prefix and not rel.startswith(prefix):
            continue
        # Путь из репозитория проверяется ТЕМИ ЖЕ правилами, что и свой: так
        # подложенный туда `.env` или путь с «..» не тронет диск.
        if is_denied(rel):
            continue
        цель = (dest / rel)
        try:
            цель_р = цель.resolve()
        except (OSError, ValueError):
            continue
        if not str(цель_р).startswith(str(dest.resolve())):
            raise GitHubError(f"{rel} ведёт за пределы папки — выкладка прервана")
        if цель.exists():
            try:
                if blob_sha(цель.read_bytes()) == sha:
                    continue
            except OSError:
                pass
        blob = api(f"/repos/{owner}/{repo}/git/blobs/{sha}", token)
        if not isinstance(blob, dict) or blob.get("encoding") != "base64":
            raise GitHubError(f"GitHub отдал {rel} в непонятном виде")
        data = base64.b64decode(blob["content"])
        if blob_sha(data) != sha:
            raise GitHubError(f"{rel} приехал повреждённым — выкладка прервана")
        цель.parent.mkdir(parents=True, exist_ok=True)
        цель.write_bytes(data)
        записано += 1
        if not quiet:
            print(f"  ✔ {rel}")
    return записано


# ── Приём правок, сделанных с телефона ───────────────────────────────────────


def load_baseline(rt: Optional[Path] = None) -> dict:
    """Слепок того, что доставила коробка (`config/box_files.json`).

    Нужен ровно для одного: отличить «файл правили снаружи» от «файл наш и его
    заменит следующий патч». Формат читаем терпимо — важно только соответствие
    путь → отпечаток.
    """
    rt = rt or root()
    try:
        raw = json.loads((rt / BASELINE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(raw, dict):
        inner = raw.get("files", raw)
        if isinstance(inner, dict):
            return {k: (v.get("sha256") if isinstance(v, dict) else v)
                    for k, v in inner.items()}
    return {}


def pull(token: str, rt: Optional[Path] = None, apply: bool = False,
         quiet: bool = False) -> dict:
    """Что изменилось в репозитории против локального дерева.

    Без `apply` НИЧЕГО не пишет. Разделение то же, что у `updater`
    (`--check` / `--apply`), и по той же причине: показ ничего не портит, а
    человек должен увидеть список ДО того, как файлы на его диске поменялись.
    """
    rt = rt or root()
    cfg = load_config(rt)
    owner, repo = cfg.get("owner"), cfg.get("repo")
    branch = cfg.get("branch") or DEFAULT_BRANCH
    if not owner or not repo:
        raise GitHubError("репозиторий ещё не заведён — запусти --setup")

    commit_sha, remote = remote_tree(token, owner, repo, branch)
    if not commit_sha:
        return {"new": [], "changed": [], "applied": 0, "conflicts": []}

    files, _skipped = collect(rt)
    local = {rel: blob_sha(data) for rel, data in files.items()}
    baseline = load_baseline(rt)

    new: list = []
    changed: list = []
    conflicts: list = []
    for rel, sha in sorted(remote.items()):
        # Путь из репозитория проверяется ТЕМИ ЖЕ правилами, что и свой: так
        # подложенный туда `.env` или путь с «..» не тронет диск клиента.
        if is_denied(rel) or not is_included(rel):
            continue
        if rel not in local:
            new.append(rel)
        elif local[rel] != sha:
            changed.append(rel)
            # Файл наш (есть в слепке коробки) и правился снаружи — про такое
            # говорим вслух: следующий патч ляжет поверх, и человек должен знать
            # это заранее, а не по пропавшей правке.
            if rel in baseline:
                conflicts.append(rel)

    if not apply:
        return {"new": new, "changed": changed, "applied": 0, "conflicts": conflicts}

    applied = 0
    backup = rt / BACKUP_DIR / FROM_GITHUB_DIR / time.strftime("%Y%m%d-%H%M%S")
    root_resolved = str(rt.resolve())
    for rel in new + changed:
        blob = api(f"/repos/{owner}/{repo}/git/blobs/{remote[rel]}", token)
        if not isinstance(blob, dict) or blob.get("encoding") != "base64":
            raise GitHubError(f"GitHub отдал {rel} в непонятном виде — приём прерван")
        data = base64.b64decode(blob["content"])
        if blob_sha(data) != remote[rel]:
            raise GitHubError(f"{rel} приехал повреждённым — приём прерван")
        target = (rt / rel).resolve()
        if not str(target).startswith(root_resolved):
            raise GitHubError(f"{rel} ведёт за пределы завода — приём прерван")
        if rel in changed:
            keep = backup / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(files[rel])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        applied += 1
        if not quiet:
            print(f"  ✔ {rel}")
    return {"new": new, "changed": changed, "applied": applied,
            "conflicts": conflicts}


# ── Командная строка ─────────────────────────────────────────────────────────


def включи(rt: Optional[Path] = None, name: str = DEFAULT_REPO_NAME) -> int:
    """ВСЁ ВКЛЮЧЕНИЕ ОДНОЙ КОМАНДОЙ: вход → репозиторий → первая копия.

    Раньше человеку надо было выполнить три команды подряд (`--login`,
    `--setup`, `--push`), каждую с путём к питону внутри папки завода. Три шанса
    ошибиться там, где содержательное действие ровно одно: «сделай копию».
    Пропустил третью — копии нет, а выглядит как будто всё настроено.

    Поэтому шаги слиты в один, и каждый повторно безопасен: вход уже есть —
    не спрашиваем заново, репозиторий уже заведён — берём его. Человек может
    запустить команду сколько угодно раз.
    """
    rt = rt or root()
    print("Включаю резервную копию завода.")
    print()

    token = get_token(rt)
    if token:
        print("  1/3  Вход в GitHub уже выполнен — пропускаю.")
    else:
        print("  1/3  Нужен вход в ваш GitHub.")
        token = login_device()
        save_token(token, rt)
        print("       ✔ Вход выполнен.")

    cfg = load_config(rt)
    if cfg.get("owner") and cfg.get("repo"):
        print(f"  2/3  Репозиторий уже есть: {cfg['owner']}/{cfg['repo']}")
        info = {"owner": cfg["owner"], "repo": cfg["repo"]}
    else:
        print("  2/3  Завожу приватный репозиторий…")
        info = ensure_repo(token, name, rt)
        print(f"       ✔ Готов: {info['owner']}/{info['repo']}")

    print("  3/3  Отправляю копию…")
    итог = push(token, rt)
    if итог["commit"] is None:
        print("       ✔ Копия уже свежая, отправлять нечего.")
    else:
        print(f"       ✔ Отправлено файлов: {итог['total']}")

    print()
    print("=" * 58)
    print("  ГОТОВО. Копия завода лежит здесь:")
    print(f"  https://github.com/{info['owner']}/{info['repo']}")
    print("=" * 58)
    print("  Она приватная — видите её только вы.")
    print("  Дальше обновляется сама, ничего делать не нужно.")
    return 0


def configured(rt: Optional[Path] = None) -> bool:
    rt = rt or root()
    cfg = load_config(rt)
    return bool(cfg.get("owner") and cfg.get("repo") and get_token(rt))


def cmd_status(rt: Path) -> int:
    cfg = load_config(rt)
    token = get_token(rt)
    print("Резервная копия завода на GitHub")
    print(f"  вход выполнен:   {'да' if token else 'НЕТ — запусти --login'}")
    if cfg.get("owner"):
        print(f"  репозиторий:     {cfg['owner']}/{cfg['repo']} "
              f"(ветка {cfg.get('branch', DEFAULT_BRANCH)})")
        print(f"  адрес:           https://github.com/{cfg['owner']}/{cfg['repo']}")
    else:
        print("  репозиторий:     НЕ ЗАВЕДЁН — запусти --setup")
    last = cfg.get("last_push") or {}
    if last:
        print(f"  последняя копия: {last.get('at')} · файлов {last.get('files')}")
    else:
        print("  последняя копия: ещё не делалась")
    return 0 if configured(rt) else 2


def cmd_list(rt: Path) -> int:
    try:
        files, skipped = collect(rt)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"Уедет файлов: {len(files)} "
          f"({sum(len(v) for v in files.values()) // 1024} КБ)")
    for rel in sorted(files)[:200]:
        print(f"  {rel}")
    if len(files) > 200:
        print(f"  … и ещё {len(files) - 200}")
    hard, soft, samples = scan_secrets(files, env_values(rt))
    if hard:
        print("\n🔴 Секреты в файлах — отправка была бы отменена:")
        for rel, num, what in hard[:20]:
            print(f"  {rel}:{num} — {what}")
        print(f"  Строка с настоящим ключом чинится переносом ключа в .env. "
              f"Строка-ОБРАЗЕЦ — отметкой «{SAMPLE_MARK}» в той же строке.")
    if soft:
        print("\n⚠️ Подозрения — эти файлы будут исключены:")
        for rel, num, what in soft[:20]:
            print(f"  {rel}:{num} — {what}")
    if samples:
        print(f"\nℹ️ Отмечено как образец и потому пропущено строк: {len(samples)}")
        for rel, num, _ in samples[:10]:
            print(f"  {rel}:{num}")
    print(f"\nОтсеяно чёрным списком: {len(skipped)}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Резервная копия завода в личном приватном репозитории GitHub")
    parser.add_argument("--включи", dest="включи", action="store_true",
                        help="ВСЁ ОДНОЙ КОМАНДОЙ: вход, репозиторий, первая копия")
    parser.add_argument("--login", action="store_true", help="войти в свой GitHub")
    parser.add_argument("--token", metavar="TOKEN",
                        help="запасной путь: вставить доступ вручную")
    parser.add_argument("--setup", action="store_true",
                        help="завести приватный репозиторий (идемпотентно)")
    parser.add_argument("--push", action="store_true", help="отправить изменения")
    parser.add_argument("--pull", action="store_true",
                        help="что изменилось в репозитории (ничего не пишет)")
    parser.add_argument("--apply", action="store_true",
                        help="вместе с --pull: применить изменения локально")
    parser.add_argument("--auto", action="store_true",
                        help="тихо, для хука: молчит, когда сказать нечего")
    parser.add_argument("--status", action="store_true", help="что настроено")
    parser.add_argument("--list", action="store_true", help="что именно уедет")
    parser.add_argument("--name", default=DEFAULT_REPO_NAME,
                        help=f"имя репозитория (по умолчанию {DEFAULT_REPO_NAME})")
    parser.add_argument("--message", default="", help="текст записи коммита")
    args = parser.parse_args(argv)

    rt = root()
    if args.status:
        return cmd_status(rt)
    if args.list:
        return cmd_list(rt)

    try:
        if args.включи:
            return включи(rt, args.name)
        if args.token:
            save_token(args.token.strip(), rt)
            print("✔ Доступ сохранён. Дальше: --setup")
            return 0
        if args.login:
            save_token(login_device(), rt)
            print("✔ Вход выполнен, доступ сохранён. Дальше: --setup")
            return 0

        token = get_token(rt)
        if args.setup:
            if not token:
                print("Сначала войди: python github_backup.py --login")
                return 1
            info = ensure_repo(token, args.name, rt)
            print(f"✔ Репозиторий готов: "
                  f"https://github.com/{info['owner']}/{info['repo']} (приватный)")
            print("  Дальше: python github_backup.py --push")
            return 0

        if args.pull:
            if not token:
                print("Сначала войди: python github_backup.py --login")
                return 1
            result = pull(token, rt, apply=args.apply)
            if not result["new"] and not result["changed"]:
                print("В репозитории нет ничего нового.")
                return 0
            if not args.apply:
                print(f"Новых файлов: {len(result['new'])}, "
                      f"изменённых: {len(result['changed'])}")
                for rel in (result["new"] + result["changed"])[:50]:
                    print(f"  {rel}")
                print("\nПрименить: python github_backup.py --pull --apply")
            else:
                print(f"✔ Применено файлов: {result['applied']}")
            if result["conflicts"]:
                print("\n⚠️ Эти файлы привезла коробка, а правили их снаружи —")
                print("   следующий патч ляжет поверх и скажет об этом вслух:")
                for rel in result["conflicts"][:20]:
                    print(f"  {rel}")
            return 0

        if args.push or args.auto:
            if not configured(rt):
                if args.auto:
                    # Хук молчать не имеет права (урок BOX-73): непойманная
                    # ненастроенность выглядела бы как работающая резервная
                    # копия. Но и повторять при каждом запуске нельзя — такое
                    # человек перестаёт читать за два дня.
                    if should_nag(rt):
                        print("ℹ️ Резервной копии завода на GitHub ещё нет — "
                              "он существует только на этом компьютере.")
                        print("   Включается один раз: python github_backup.py --login")
                    return 0
                print("Не настроено. По порядку: --login, затем --setup")
                return 1
            result = push(token, rt, quiet=args.auto, message=args.message,
                          max_changed=AUTO_MAX_CHANGED if args.auto else None)
            if result.get("чужие_правки"):
                # Говорим всегда, даже в --auto: иначе копия молча перестаёт
                # обновляться, а человек уверен, что она свежая.
                print("ℹ️ На GitHub есть правки, которых нет на этом компьютере —")
                print("   похоже, вы работали с телефона. Чтобы не затереть их,")
                print("   копию не отправляю.")
                print("   Заберите их сначала: скажите «забери правки с телефона».")
                return 0
            if result.get("too_many"):
                print(f"ℹ️ Изменилось файлов: {result['too_many']} — это больше, чем "
                      "стоит отправлять в фоне.")
                print("   Отправь одной командой, она может занять минуту: "
                      "python github_backup.py --push")
                return 0
            if result["commit"] is None:
                if not args.auto:
                    print("Изменений нет — копия и так свежая.")
                return 0
            print(f"✔ Копия обновлена: изменено {result['changed']}"
                  + (f", удалено {result['removed']}" if result["removed"] else "")
                  + f", всего в репозитории {result['total']}")
            return 0

        parser.print_help()
        return 2

    except GitHubError as exc:
        if args.auto and str(exc).startswith("сеть недоступна"):
            return 0          # нет интернета — не повод портить день клиенту
        print(f"❌ {exc}")
        return 1
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
