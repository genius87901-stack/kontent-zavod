# -*- coding: utf-8 -*-
# Докстринг ниже — «сырая» строка (r"""): в тексте есть windows-пути вида
# python\python.exe и .venv\Scripts\python.exe, а `\p` и `\S` — не escape-
# последовательности, и Python 3.12 печатает на них SyntaxWarning при КАЖДОМ
# запуске. У клиента это первый же вывод мастера: жёлтое предупреждение до
# единой строчки по делу.
r"""
check_env — проверка окружения перед установкой (шаг 1).

Проверяет то, что НЕ ставится через pip и должно быть в системе заранее:
    - Python нужной версии (окно версий считается из манифестов, см. ниже);
    - git — ТОЛЬКО на машине разработки (см. «GIT НЕ КРИТИЧЕН У КЛИЕНТА» ниже).

GIT НЕ КРИТИЧЕН У КЛИЕНТА. Обновления коробка получает по HTTPS через Contents API
(updater.py), git там не участвует ВООБЩЕ. На чистой Windows git не стоит никогда,
и объявлять его нехваткой значило бы гнать нетехнического клиента ставить git из
интернета — там, где обещан офлайн. Поэтому git критичен только тогда, когда рядом
с корнем есть git-репозиторий (`.git/`), то есть на машине разработчика/оркестратора,
где им действительно собирают и синхронизируют. У клиента `.git/` нет: сборщик его
не кладёт. Форсировать проверку можно флагом --require-git.

ПИТОН МОЖЕТ ЛЕЖАТЬ ВНУТРИ САМОЙ КОРОБКИ, А НЕ В PATH. Windows-поставка приносит
свой интерпретатор (`python\python.exe`) и разворачивает `.venv\Scripts\python.exe`
— ни того, ни другого в PATH нет и не будет. При этом на Windows 10/11 команда
`python` в PATH ЕСТЬ всегда: это заглушка Microsoft Store, которая питоном не
является и на `-c` отвечает ошибкой. Поэтому поиск только по PATH давал «питона
нет» на машине, где питон лежит в двух шагах. Каталоги коробки просматриваются
ПОСЛЕ PATH и только если по PATH подходящего питона не нашлось (см. box_interpreters).

ЦЕЛЕВАЯ ВЕРСИЯ PYTHON — ОДНА КОНСТАНТА (TARGET_PYTHON ниже). Из неё выводятся все
команды-починки (winget/brew/apt) и подсказки; больше версия нигде не зашита —
ни в stages.json, ни в SKILL.md. Смена целевой версии = правка одной строки здесь.

ОКНО ВЕРСИЙ, А НЕ ТОЛЬКО НИЖНЯЯ ГРАНИЦА. `python_requires` вида ">=3.9,<3.13"
разбирается ЦЕЛИКОМ: верхняя граница уважается. Иначе свежий brew-питон (3.14)
проходит проверку, а потом клиент упирается в отсутствие колёс mediapipe.

КАКИМ ПИТОНОМ СТАВИТЬ — РЕШАЕТ ЭТОТ СКРИПТ, А НЕ МАСТЕР. В --json есть поле
python.recommended: {name, path, version} — интерпретатор, которым надо ставить
завод (если текущий подходит, там он и будет). Мастеру запрещено угадывать имена
вроде «python3.12»: на маке `python3` — это системный 3.9, а подходящий 3.11
лежит в каталоге Homebrew, который может быть ниже по PATH или не быть в нём вовсе.

СИСТЕМНЫЙ ffmpeg НЕ обязателен: движок берёт ffmpeg из pip-пакета imageio-ffmpeg,
поэтому здесь он лишь ЖЕЛАТЕЛЕН — если есть, отметим; если нет, это не блокер.
Прочие system_binaries из манифестов проверяются мягко: сообщаем, чего нет и как
поставить, но не валимся.

ДВЕ ВЕТКИ ОС (BOX-18). Основная целевая ОС клиента — Windows 10/11; часть клиентов
на macOS. Скрипт честно определяет ОС и выдаёт команду-починку ИМЕННО для неё:
    Windows  → winget install ...
    macOS    → brew install ... (и подсказка, как поставить сам Homebrew)
    Linux    → apt/dnf (не целевая ОС, но не врём: подсказку даём)
Флаг --assume-os {windows,macos,linux} показывает подсказки чужой ОС, не подменяя
факты о текущей машине (для проверки обеих веток разработчиком).

Скрипт НИЧЕГО не качает и в сеть не ходит: только смотрит, что уже стоит.

Печатает по-русски человеческим языком, что делать. Код возврата:
    0 — всё критичное на месте, можно ставить;
    1 — не хватает критичного (Python; git — только на машине разработки) —
        установить и повторить.

Только stdlib. Кроссплатформенно.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Консоль русской Windows — cp1251, и ЛЮБОЙ значок вне неё (→ ✓ ✗ • ⚠) роняет
# скрипт UnicodeEncodeError прямо в лицо клиенту. Переводим свой вывод в utf-8
# с заменой непечатаемого: ни один текст мастера больше не может убить установку.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # старый питон или подменённый stdout — не критично
    pass



# ── ЦЕЛЕВАЯ ВЕРСИЯ PYTHON — ЕДИНСТВЕННЫЙ ИСТОЧНИК ПРАВДЫ ─────────────────────
# Отсюда собираются ВСЕ подсказки установки питона (winget/brew/apt) и отсюда же
# берётся нижняя граница по умолчанию, если манифесты молчат. Паритет с уже
# обкатанной Windows-коробкой: installer/manifest.json → python_embed 3.11.9,
# ABI-тег cp311. Меняется целевая версия — меняется ТОЛЬКО эта строка.
TARGET_PYTHON = (3, 11)


def vstr(v) -> str:
    return ".".join(map(str, v))


TARGET = vstr(TARGET_PYTHON)          # "3.11" — подставляется в команды ниже


# ── ОС и её профиль ──────────────────────────────────────────────────────────
OS_PROFILES = {
    "windows": {
        "title": "Windows",
        "pkg_manager": None,          # winget встроен в Windows 10/11
        "python": f"winget install Python.Python.{TARGET}  (или скачайте с python.org)",
        "git": "winget install Git.Git",
        "ffmpeg": "winget install Gyan.FFmpeg",
        "generic": "winget install {name}",
        "notes": [],
    },
    "macos": {
        "title": "macOS",
        "pkg_manager": {
            "name": "Homebrew",
            "check": "brew",
            "install": 'установите Homebrew — на сайте brew.sh есть одна команда '
                       'для Терминала; на Apple Silicon установщик в конце просит '
                       'дописать строку с «brew shellenv» в ~/.zprofile — это тоже '
                       'нужно сделать, иначе brew не найдётся',
            "why": "на маке через него ставится всё системное: питон, git, ffmpeg",
        },
        "python": f"brew install python@{TARGET}  (или скачайте с python.org)",
        "git": "xcode-select --install  (или brew install git)",
        "ffmpeg": "brew install ffmpeg",
        "generic": "brew install {name}",
        "notes": [
            # Раньше здесь обещали, что в коробке лежат файлы .bat «только для
            # Windows». Их там нет ни на одной ОС: сборщик вырезает все .bat
            # чёрным списком (build_box.py, FORBIDDEN_NAME_RE). Обещание убрано.
            "Терминал открывать не нужно: все команды на маке выполняет помощник.",
            "Если коробку скачали архивом из интернета, macOS может пометить её как "
            "«из карантина». Тогда помощник один раз выполнит: "
            "xattr -dr com.apple.quarantine <папка завода>.",
            "Папка Google Диска на маке лежит не в «Мой компьютер», а обычно в "
            "~/Library/CloudStorage/GoogleDrive-<почта>/Мой диск — мастер спросит и "
            "предложит найденные варианты.",
        ],
    },
    "linux": {
        "title": "Linux",
        "pkg_manager": None,
        "python": f"sudo apt install python{TARGET} python{TARGET}-venv  "
                  f"(или dnf install python{TARGET})",
        "git": "sudo apt install git",
        "ffmpeg": "sudo apt install ffmpeg",
        "generic": "sudo apt install {name}",
        "notes": [
            "Linux — не целевая ОС коробки: поддержка «как получится», основной путь "
            "Windows, второй — macOS.",
        ],
    },
}

# Ключ в манифесте с подсказкой установки — свой на каждую ОС; windows_install
# остаётся историческим значением по умолчанию.
MANIFEST_HINT_KEYS = {
    "windows": ("windows_install", "install"),
    "macos": ("macos_install", "mac_install", "brew_install", "install"),
    "linux": ("linux_install", "apt_install", "install"),
}


def detect_os() -> str:
    sysname = platform.system().strip().lower()
    if sysname == "darwin":
        return "macos"
    if sysname == "windows":
        return "windows"
    return "linux"


def profile(os_key: str) -> dict:
    return OS_PROFILES.get(os_key, OS_PROFILES["linux"])


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def _version_token(s: str) -> tuple[int, ...] | None:
    """Первое число вида 3.11 из куска спецификации."""
    digits = "".join(c if (c.isdigit() or c == ".") else " " for c in s).split()
    for tok in digits:
        parts = tok.split(".")
        if parts and parts[0].isdigit():
            return tuple(int(p) for p in parts if p.isdigit())
    return None


def parse_min_version(s: str) -> tuple[int, ...]:
    # ">=3.11" -> (3, 11). Историческая функция: только нижняя граница.
    # Оставлена ради совместимости — новый код зовёт parse_requires().
    return _version_token(s) or TARGET_PYTHON


def parse_requires(s: str) -> tuple[tuple[int, ...] | None, tuple[int, ...] | None]:
    """">=3.9,<3.13" → ((3, 9), (3, 13)) — нижняя граница ВКЛЮЧИТЕЛЬНО, верхняя
    ИСКЛЮЧИТЕЛЬНО.

    Раньше верхняя граница терялась: из ">=3.9,<3.13" бралось только (3, 9), и
    свежий питон 3.14 проходил проверку — а колёс mediapipe/onnxruntime под него
    нет, и клиент упирался в это уже ПОСЛЕ самого долгого шага установки.
    '<=3.12' приводится к исключительной границе (3, 12, 999999).
    """
    lo = hi = None
    for part in str(s).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        v = _version_token(part)
        if not v:
            continue
        if part.startswith("<="):
            v = (*v, 999999)
            hi = v if hi is None else min(hi, v)
        elif part.startswith("<"):
            hi = v if hi is None else min(hi, v)
        elif part.startswith("=="):
            lo = v if lo is None else max(lo, v)
            hi_v = (*v, 999999)
            hi = hi_v if hi is None else min(hi, hi_v)
        elif part.startswith((">=", ">", "~=", "^")):
            lo = v if lo is None else max(lo, v)
        else:
            lo = v if lo is None else max(lo, v)
    return lo, hi


def required_python_range(root: Path) -> tuple[tuple[int, ...], tuple[int, ...] | None]:
    """Окно версий из всех манифестов: (минимум включительно, максимум исключительно).

    Минимум — самый строгий из python_requires, но не ниже TARGET_PYTHON.
    Максимум — самый строгий из верхних границ (None, если её никто не задал).
    """
    lo, hi = TARGET_PYTHON, None
    mod_dir = root / "modules"
    if mod_dir.is_dir():
        for man in mod_dir.glob("*/module.manifest.json"):
            try:
                data = json.loads(man.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            pr = data.get("python_requires")
            if not pr:
                continue
            mn, mx = parse_requires(pr)
            if mn:
                lo = max(lo, mn)
            if mx:
                hi = mx if hi is None else min(hi, mx)
    return lo, hi


def required_python(root: Path) -> tuple[int, ...]:
    """Максимум из python_requires всех манифестов, иначе TARGET_PYTHON."""
    return required_python_range(root)[0]


def version_fits(v, lo, hi) -> bool:
    """Версия v попадает в окно [lo, hi)."""
    if not v:
        return False
    if tuple(v) < tuple(lo):
        return False
    if hi and tuple(v) >= tuple(hi):
        return False
    return True


# ── ЧИП МАКА: единственный вопрос, который может отменить всю установку ───────
# modules/engine_media пинит torch==2.8.0 / torchaudio==2.8.0 / torchvision==0.23.0
# строго через '=='. Колёс под macOS x86_64 у этих версий НЕТ ВООБЩЕ — на PyPI
# только macosx_11_0_arm64 (cp39…cp313). Пин строгий, и make_venv отдельно роняет
# установку на любой незапиненной строке (CONTRACT.md §3) — значит pip не имеет
# права взять torch постарше.
#
# Что было без этой проверки. Intel-мак проходил Шаг 1 «зелёным», клиент 20-40
# минут ставил Homebrew и Xcode CLT, доходил до Шага 4 (самого долгого) и получал
# «ERROR: Could not find a version that satisfies the requirement torch==2.8.0
# (from versions: none)» — красную стену текста и «установка не удалась» без
# единого слова о причине. Ошибка честная, но она про чип, а не про интернет.
#
# Проверка НЕ делает Intel-мак пригодным: установка на нём по-прежнему невозможна.
# Она делает отказ РАННИМ и ЧЕЛОВЕЧЕСКИМ — до Homebrew, до гигабайтов трафика.
MAC_ARM_ONLY_PIP = ("torch", "torchaudio", "torchvision")


def _mac_is_translated() -> bool:
    """Запущены ли мы под Rosetta 2 (x86_64-питон на Apple Silicon).

    Важная тонкость: под Rosetta `platform.machine()` честно отвечает "x86_64",
    хотя чип — arm64. Наказывать такого клиента отказом «у вас Intel» было бы
    ложью: у него подходящий Mac, просто питон не тот. Отличить можно только
    через sysctl: ядро выставляет sysctl.proc_translated=1 именно для
    транслированных процессов.
    """
    try:
        r = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (r.stdout or "").strip() == "1"


def arm_only_packages(root: Path) -> list[str]:
    """Пины из манифестов, у которых нет колёс под macOS x86_64 — как есть.

    Список читается из manifest-ов, а не зашит: сменится пин torch — сменится и
    текст отказа, а выдуманных пакетов в нём не появится.
    """
    found: list[str] = []
    mod_dir = root / "modules"
    if not mod_dir.is_dir():
        return found
    for man in sorted(mod_dir.glob("*/module.manifest.json")):
        try:
            data = json.loads(man.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        for spec in data.get("pip", []) or []:
            name = str(spec).split("==")[0].strip().lower().replace("_", "-")
            if name in MAC_ARM_ONLY_PIP and spec not in found:
                found.append(str(spec))
    return found


def macos_arch_verdict(root: Path, *, os_key: str | None = None,
                       machine: str | None = None,
                       translated: bool | None = None) -> dict:
    """Годится ли ЭТОТ Mac для установки. Единственный ответ на вопрос о чипе.

    Именованные аргументы существуют ради проверяемости: тест обязан уметь
    спросить вердикт для Intel-мака, не имея Intel-мака. В боевом вызове их не
    передают — всё берётся с текущей машины.

    Возвращает dict:
        ok         — можно ставить;
        arch       — что ответила машина ("arm64" / "x86_64" / …);
        translated — процесс идёт под Rosetta 2;
        blockers   — пины, из-за которых отказ (пусто при ok=True);
        reason     — почему нельзя, человеческой фразой;
        fix        — что делать человеку.
    Для не-macOS всегда ok=True: проверка ничего не знает о других ОС и не имеет
    права мешать им ставиться.
    """
    os_key = os_key or detect_os()
    if os_key != "macos":
        return {"ok": True, "arch": machine or platform.machine(),
                "translated": False, "blockers": [], "reason": "", "fix": ""}

    arch = (machine if machine is not None else platform.machine()).strip().lower()
    blockers = arm_only_packages(root)
    if arch in ("arm64", "aarch64"):
        return {"ok": True, "arch": arch, "translated": False, "blockers": [],
                "reason": "", "fix": ""}
    if not blockers:
        # Пины сменились и арм-онли пакетов больше нет — отказывать не за что.
        return {"ok": True, "arch": arch, "translated": bool(translated),
                "blockers": [], "reason": "", "fix": ""}

    is_tr = _mac_is_translated() if translated is None else bool(translated)
    if is_tr:
        return {
            "ok": False, "arch": arch, "translated": True, "blockers": blockers,
            "reason": "установка запущена питоном для процессоров Intel, хотя сам "
                      "Mac — Apple Silicon (система запустила его через Rosetta). "
                      "Такому питону нужные пакеты не поставить: их выпускают "
                      "только для Apple Silicon",
            "fix": "поставить питон для Apple Silicon и повторить установку им: "
                   f"brew install python@{TARGET} (Homebrew на Apple Silicon "
                   "живёт в /opt/homebrew — если brew стоит в /usr/local, он тоже "
                   "интеловый, и его надо переставить)",
        }
    return {
        "ok": False, "arch": arch, "translated": False, "blockers": blockers,
        "reason": "у этого Mac процессор Intel, а завод собран под Mac с чипом "
                  "Apple (M1/M2/M3/M4). Часть движка — распознавание речи и "
                  "работа с видео — выпускается только для чипов Apple, и на "
                  "Intel её взять негде: "
                  + ", ".join(blockers),
        "fix": "установка на этом компьютере невозможна — дело не в настройках и "
               "не в интернете. Проверить чип можно так: меню Apple (яблоко в "
               "левом верхнем углу) → «Об этом Mac» → строка «Чип». Нужно "
               "«Apple M1» и новее. Напишите нам эту строку — подберём вариант",
    }


def extra_binaries(root: Path) -> list[dict]:
    """system_binaries из манифестов (кроме ffmpeg, который необязателен)."""
    out, seen = [], set()
    mod_dir = root / "modules"
    if not mod_dir.is_dir():
        return out
    for man in mod_dir.glob("*/module.manifest.json"):
        try:
            data = json.loads(man.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        for b in data.get("system_binaries", []) or []:
            name = (b.get("name") or "").strip().lower()
            if name and name != "ffmpeg" and name not in seen:
                seen.add(name)
                out.append(b)
    return out


def manifest_hint(b: dict, os_key: str) -> str:
    for k in MANIFEST_HINT_KEYS.get(os_key, ("install",)):
        v = (b.get(k) or "").strip()
        if v:
            return v
    return profile(os_key)["generic"].format(name=b.get("name", ""))


# Стандартные каталоги Homebrew. Это НЕ путь конкретной машины, а фиксированные
# места установки самого менеджера пакетов: /opt/homebrew — Apple Silicon,
# /usr/local — Intel-маки. Нужны потому, что на Apple Silicon brew попадает в PATH
# только через `eval "$(brew shellenv)"` в ~/.zprofile, а уже запущенная сессия
# (в том числе сессия помощника) этого PATH не увидит, сколько Терминал ни
# перезапускай. Без этой подстраховки мастер объявил бы «brew не найден» при
# установленном brew и «нет питона 3.11» при установленном 3.11.
MAC_EXTRA_PATH = ("/opt/homebrew/bin", "/opt/homebrew/sbin",
                  "/usr/local/bin", "/usr/local/sbin")


def search_path(os_key: str) -> str | None:
    """PATH для поиска бинарников. На Windows/Linux — как было (None = штатный
    PATH процесса), на macOS — PATH процесса плюс каталоги Homebrew."""
    if os_key != "macos":
        return None
    parts = [p for p in (os.environ.get("PATH", "") or "").split(os.pathsep) if p]
    for d in MAC_EXTRA_PATH:
        if d not in parts:
            parts.append(d)
    return os.pathsep.join(parts)


def find_bin(name: str, os_key: str | None = None) -> str | None:
    """Полный путь к бинарнику (или None). Мастеру нужен именно путь: вызывать
    brew/питон по полному пути надёжнее, чем надеяться на PATH сессии."""
    if not name:
        return None
    return shutil.which(name, path=search_path(os_key) if os_key else None)


def bin_ok(check_cmd: str, fallback_name: str, os_key: str | None = None) -> bool:
    if fallback_name and find_bin(fallback_name, os_key):
        return True
    if not check_cmd:
        return False
    try:
        r = subprocess.run(check_cmd, shell=True, capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# Имена, под которыми на машине может лежать питон. Список — от свежего к старому;
# python{TARGET} в нём есть всегда (окно 3.11…3.15 покрывает целевую версию).
PY_SCAN_NAMES = ["python3.15", "python3.14", "python3.13", "python3.12", "python3.11",
                 "python3", "python"]


def probe_python(exe: str) -> tuple[int, ...] | None:
    """Спросить у интерпретатора его версию. Никакого угадывания по имени файла:
    `python3` на маке — это 3.9, а `python3.11` из Homebrew может вообще не быть
    в PATH сессии."""
    try:
        r = subprocess.run(
            [exe, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if r.returncode != 0:
        return None
    return _version_token((r.stdout or "").strip())


def python_interpreters(os_key: str, lo=None, hi=None) -> list[dict]:
    """Питоны на машине с РЕАЛЬНЫМИ версиями: [{name, path, version, ok}].

    ok — версия попадает в окно [lo, hi). В сеть не ходим, ничего не ставим.
    """
    names = ["py", *PY_SCAN_NAMES] if os_key == "windows" else list(PY_SCAN_NAMES)
    out, seen = [], set()
    for n in names:
        p = find_bin(n, os_key)
        if not p or p in seen:
            continue
        seen.add(p)
        v = probe_python(p)
        out.append({"name": n, "path": p,
                    "version": vstr(v) if v else None,
                    "ok": bool(lo and version_fits(v, lo, hi))})
    return out


def python_candidates(os_key: str) -> list[str]:
    """Имена питонов на машине (историческая функция, совместимость)."""
    return [c["name"] for c in python_interpreters(os_key)]


# Питоны, которые коробка приносит С СОБОЙ. Это НЕ пути конкретной машины, а
# фиксированные места внутри папки установки — их создаёт установщик
# (installer/kontent-zavod.iss: `{app}\python\python.exe` и
# `{app}\.venv\Scripts\python.exe`). В PATH они не попадают никогда, поэтому
# shutil.which их не видит, и на чистой Windows проверка объявляла «питона нет»
# при двух рабочих питонах внутри папки.
#
# Порядок = приоритет. `.venv` первым: это уже развёрнутое окружение завода со
# всеми пакетами. `python\` (встраиваемая сборка) — запасной вариант, ею коробка
# только поднимает `.venv`, и брать её, когда `.venv` уже есть, незачем.
BOX_PYTHON_RELPATHS = {
    "windows": (".venv/Scripts/python.exe", "python/python.exe"),
    "macos": (".venv/bin/python3", ".venv/bin/python"),
    "linux": (".venv/bin/python3", ".venv/bin/python"),
}


def box_interpreters(root: Path, os_key: str, lo=None, hi=None) -> list[dict]:
    """Питоны ВНУТРИ папки установки, с реально спрошенными версиями.

    Формат записи тот же, что у python_interpreters, плюс `in_box: True` —
    мастеру полезно знать, что доустанавливать ничего не надо.
    """
    out, seen = [], set()
    for rel in BOX_PYTHON_RELPATHS.get(os_key, ()):
        p = root.joinpath(*rel.split("/"))
        try:
            if not p.is_file():
                continue
            real = str(p.resolve())
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        v = probe_python(real)
        out.append({"name": rel, "path": real,
                    "version": vstr(v) if v else None,
                    "ok": bool(lo and version_fits(v, lo, hi)),
                    "in_box": True})
    return out


def all_interpreters(root: Path, os_key: str, lo=None, hi=None) -> list[dict]:
    """Питоны по PATH, а если подходящего среди них нет — ещё и питоны коробки.

    Каталоги коробки просматриваются ТОЛЬКО как запасной вариант. Так мак-ветка
    (там подходящий brew-питон по PATH есть) не может измениться вообще: до
    box-ветки исполнение просто не доходит.
    """
    cands = python_interpreters(os_key, lo, hi)
    if any(c.get("ok") for c in cands):
        return cands
    known = {c["path"] for c in cands}
    return cands + [c for c in box_interpreters(root, os_key, lo, hi)
                    if c["path"] not in known]


def best_interpreter(cands: list[dict]) -> dict | None:
    """Самый свежий питон, попадающий в окно версий."""
    fit = [c for c in cands if c.get("ok") and c.get("version")]
    if not fit:
        return None
    return max(fit, key=lambda c: _version_token(c["version"]) or (0,))


def window_text(lo, hi) -> str:
    """Окно версий питона одной человеческой фразой из уже готовых lo/hi.

    `install/make_venv.py:177` зовёт именно эту сигнатуру (lo/hi уже на руках
    из `required_python_range`, звать `version_window(root)` второй раз незачем)
    — а функции не было вовсе, только `version_window(root)`. Тот же класс
    дыры, что BOX-42 (`interpreter_for`): AttributeError на ветке «уже собранное
    .venv на негодном питоне», которую `make_venv.py --dry-run` не проверяет.
    """
    if hi:
        return f"Python от {vstr(lo)} до {vstr(hi)} (сама {vstr(hi)} уже не подходит)"
    return f"Python {vstr(lo)} или новее"


def version_window(root: Path) -> str:
    """Окно версий питона одной человеческой фразой (для сообщений установщика)."""
    return window_text(*required_python_range(root))


def interpreter_for(root: Path, explicit: str | None = None) -> dict:
    """Каким интерпретатором строить `.venv`. Единственный ответ на этот вопрос.

    BOX-42: `install/make_venv.py:147` звал эту функцию, а её не существовало —
    `AttributeError` ронял Шаг 4 установки (создание окружения) на ЛЮБОЙ ОС у
    каждого нового клиента. Правила выбора НЕ переписаны: функция собирает
    ответ из тех же `required_python_range` / `all_interpreters` /
    `best_interpreter` / `version_fits`, что и `main()`, — второй копии правила
    в проекте не появилось, только другая упаковка для другого потребителя.

    Возвращает dict с полями, которые читает `make_venv.py`:
        ok      — можно ли вообще строить окружение (нашёлся подходящий питон);
        reason  — почему нельзя (пусто при ok=True);
        fix     — что человеку сделать (команда установки под его ОС);
        path    — каким интерпретатором строить;
        version — его версия строкой ("3.12.10");
        window  — требуемое окно версий человеческой фразой;
        current — True, если это тот питон, которым запущен текущий процесс
                  (тогда `make_venv` строит окружение сразу; иначе печатает
                  готовую команду перезапуска и НЕ подменяет интерпретатор молча);
        in_box  — питон лежит внутри папки завода (клиенту ставить нечего).
    """
    os_key = detect_os()
    lo, hi = required_python_range(root)
    window = version_window(root)
    fix = profile(os_key)["python"]

    # Назвали интерпретатор явно — он и решает. Спорить с явным указанием нельзя
    # (общее правило проекта: явный флаг сильнее автоподбора), но соврать про
    # непригодность обязаны.
    if explicit:
        v = probe_python(explicit)
        if v is None:
            return {"ok": False, "current": False, "in_box": False,
                    "path": explicit, "version": None, "window": window,
                    "reason": f"указанный питон не отвечает своей версией: {explicit}",
                    "fix": fix}
        fits = version_fits(v, lo, hi)
        try:
            same = Path(explicit).resolve() == Path(sys.executable).resolve()
        except OSError:
            same = False
        return {"ok": bool(fits), "current": same, "in_box": False,
                "path": explicit, "version": vstr(v), "window": window,
                "reason": "" if fits else
                          f"указанный питон {vstr(v)} не попадает в окно: нужен {window}",
                "fix": fix}

    # Текущий подходит — им и строим, ничего искать не надо.
    have = sys.version_info[:3]
    if version_fits(have, lo, hi):
        return {"ok": True, "current": True, "in_box": False,
                "path": sys.executable, "version": vstr(have),
                "window": window, "reason": "", "fix": fix}

    # Не подходит — ищем на машине и внутри коробки (порядок задан all_interpreters).
    best = best_interpreter(all_interpreters(root, os_key, lo, hi))
    if best:
        return {"ok": True, "current": False, "in_box": bool(best.get("in_box")),
                "path": best["path"], "version": best["version"],
                "window": window, "reason": "", "fix": fix}

    return {"ok": False, "current": False, "in_box": False,
            "path": None, "version": vstr(have), "window": window,
            "reason": f"на этом компьютере нет подходящего питона: нужен {window}, "
                      f"а найден только {vstr(have)}",
            "fix": fix}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Проверка окружения перед установкой.")
    ap.add_argument("--root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--assume-os", choices=sorted(OS_PROFILES),
                    help="Показать подсказки для другой ОС (для проверки веток "
                         "разработчиком). Факты о текущей машине не подменяются.")
    ap.add_argument("--require-git", action="store_true",
                    help="Считать git критичным даже вне git-репозитория "
                         "(машина сборки/оркестратора).")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    real_os = detect_os()
    os_key = args.assume_os or real_os
    prof = profile(os_key)

    report = {
        "os": os_key,
        "os_detected": real_os,
        "os_title": prof["title"],
        "os_release": platform.release(),
        "assume_os": bool(args.assume_os),
        "critical_ok": True,
        "cpu": {},
        "python": {},
        "git": {},
        "ffmpeg": {},
        "pkg_manager": {},
        "binaries": [],
        "notes": list(prof["notes"]),
    }
    lines = [f"Операционная система: {prof['title']} ({platform.release()})."]
    if args.assume_os and args.assume_os != real_os:
        lines.append(f"(режим проверки: подсказки показаны для {prof['title']}, "
                     f"а машина на самом деле {profile(real_os)['title']})")

    # ── ЧИП (macOS): САМАЯ ПЕРВАЯ проверка ───────────────────────────────────
    # Первая намеренно: это единственный вопрос, ответ на который может отменить
    # установку целиком. Спрашивать его после Homebrew (20-40 минут и 1-2 ГБ)
    # значит потратить чужое время впустую. Про ЧУЖУЮ ОС не врём: вердикт всегда
    # считается по РЕАЛЬНОЙ машине (real_os), --assume-os меняет только подсказки.
    arch = macos_arch_verdict(root, os_key=real_os)
    report["cpu"] = {"arch": arch["arch"], "ok": arch["ok"],
                     "translated": arch["translated"],
                     "blockers": arch["blockers"],
                     "reason": arch["reason"], "fix": arch["fix"]}
    if real_os == "macos" and arch["ok"]:
        lines.append(f"[ок] Процессор: {arch['arch']} — этот Mac подходит.")
    elif not arch["ok"]:
        report["critical_ok"] = False
        lines.append(f"[нет] {arch['reason']}.")
        lines.append(f"  Что делать: {arch['fix']}.")
        # Дальше считать бессмысленно: питон, brew и ffmpeg ничего не меняют,
        # когда пакетов под этот процессор не существует. Но и обрывать отчёт
        # нельзя — мастер читает --json целиком.

    # ── менеджер пакетов (только там, где он нужен: macOS/Homebrew) ──────────
    pm = prof.get("pkg_manager")
    if pm:
        pm_path = find_bin(pm["check"], real_os)
        pm_ok = bool(pm_path) or bin_ok(f"{pm['check']} --version", "", real_os)
        report["pkg_manager"] = {"name": pm["name"], "ok": pm_ok, "required": False,
                                 "path": pm_path}
        if pm_ok:
            lines.append(f"[ок] {pm['name']} на месте — {pm['why']}.")
        else:
            lines.append(
                f"- {pm['name']} не найден. Он не обязателен сам по себе, но {pm['why']}. "
                f"Если ниже чего-то не хватит — {pm['install']}."
            )

    # ── Python ───────────────────────────────────────────────────────────────
    need, need_max = required_python_range(root)
    have = sys.version_info[:3]
    py_ok = version_fits(have, need, need_max)
    cands = all_interpreters(root, real_os, need, need_max)
    if py_ok:
        # Текущий подходит — им и ставить. Так мастеру не надо ничего выбирать.
        recommended = {"name": Path(sys.executable).name, "path": sys.executable,
                       "version": vstr(have), "current": True}
    else:
        best = best_interpreter(cands)
        recommended = dict(best, current=False) if best else None
    report["python"] = {"need": vstr(need),
                        "have": vstr(have),
                        "ok": py_ok,
                        "fix": prof["python"],
                        # Только те, кто РЕАЛЬНО ответил своей версией. Команда
                        # `python` на Windows 10/11 есть всегда — это заглушка
                        # Microsoft Store, а не питон: в «найденное» её включать
                        # нельзя, иначе мастер решит, что питон на машине есть.
                        "found": [c["name"] for c in cands if c.get("version")],
                        # ── новое (мастер берёт ОТСЮДА, а не угадывает имена) ──
                        "target": TARGET,
                        "less_than": vstr(need_max) if need_max else None,
                        "candidates": cands,
                        "recommended": recommended,
                        # True → ставить НЕЧЕГО, надо просто перезапустить установку
                        # интерпретатором recommended.path (типовой случай мака).
                        "switch_interpreter": bool(not py_ok and recommended)}
    if py_ok:
        lines.append(f"[ок] Python {'.'.join(map(str, have))} — подходит.")
    else:
        report["critical_ok"] = False
        if recommended:
            # СТАВИТЬ НЕЧЕГО. Подходящий питон уже есть — установку просто запустили
            # не тем. Раньше здесь первой строкой шло «✗ Python слишком старый …
            # winget install …», и клиент, у которого нужный питон лежит внутри
            # папки завода, шёл ставить питон из интернета. Про «доустановить»
            # говорим только там, где ставить действительно нечем (ветки ниже).
            where = ("внутри папки завода" if recommended.get("in_box")
                     else "на компьютере")
            lines.append(
                f"- Подходящий питон уже есть — {where}: {recommended['path']} "
                f"(версия {recommended['version']}). Доустанавливать ничего не "
                f"надо: установку нужно повторить именно им."
            )
        elif need_max and tuple(have) >= tuple(need_max):
            # Слишком НОВЫЙ: колёс mediapipe/onnxruntime под него ещё нет.
            # Состояние НОВОЕ (раньше такой питон молча проходил проверку), поэтому
            # старую строку про «запущено старым» здесь не печатаем — она бы врала.
            lines.append(
                f"[нет] Python слишком новый: нужен от {vstr(need)} до {vstr(need_max)} "
                f"(не включая), а запущен {vstr(have)}. Под такую версию ещё нет "
                f"готовых пакетов движка. Что сделать: {prof['python']} — "
                f"и запустите установку заново."
            )
        else:
            lines.append(
                f"[нет] Python слишком старый: нужен {'.'.join(map(str, need))} и новее, "
                f"а запущен {'.'.join(map(str, have))}. Что сделать: {prof['python']} — "
                f"и запустите установку заново."
            )
            others = report["python"]["found"]
            if others:
                lines.append(
                    "  На компьютере уже есть команды питона: "
                    + ", ".join(others)
                    + ". Возможно, свежий питон стоит, но установка запущена старым."
                )

    # ── git ──────────────────────────────────────────────────────────────────
    # Критичен ТОЛЬКО на машине разработки. Коробке клиента git не нужен ни для
    # чего: обновления приходят по HTTPS (updater.py), синхронизация медиа — через
    # Google Диск. Признак машины разработки — git-репозиторий рядом с корнем.
    git_ok = bin_ok("git --version", "git", real_os)
    git_required = bool(args.require_git or (root / ".git").exists())
    report["git"] = {"ok": git_ok, "required": git_required, "fix": prof["git"],
                     "path": find_bin("git", real_os)}
    git_blocking = git_required and not git_ok
    if git_ok:
        lines.append("[ок] git на месте.")
    elif git_required:
        report["critical_ok"] = False
        lines.append(f"[нет] Нет git — на машине разработки он нужен для сборки и "
                     f"работы с репозиторием. Что сделать: {prof['git']} — и повторите.")
    else:
        lines.append(
            "- git не найден — он и не нужен. Обновления завод получает сам, по "
            "защищённой ссылке, а материалы приходят через Google Диск. "
            "Установка продолжается."
        )

    # ── ffmpeg (желателен, не блокер) ────────────────────────────────────────
    ff_ok = bin_ok("ffmpeg -version", "ffmpeg", real_os)
    report["ffmpeg"] = {"ok": ff_ok, "required": False, "fix": prof["ffmpeg"],
                        "path": find_bin("ffmpeg", real_os)}
    if ff_ok:
        lines.append("[ок] Системный ffmpeg найден (пригодится, но не обязателен).")
    else:
        lines.append(
            "- Системного ffmpeg нет — и не нужно: движок берёт ffmpeg из пакета "
            f"imageio-ffmpeg. Установка продолжится. Если всё же захотите системный: "
            f"{prof['ffmpeg']}."
        )

    # ── прочие бинарники из манифестов (мягко) ───────────────────────────────
    for b in extra_binaries(root):
        ok = bin_ok(b.get("check", ""), b.get("name", ""), real_os)
        hint = manifest_hint(b, os_key)
        report["binaries"].append({"name": b.get("name"), "ok": ok, "fix": hint,
                                   "path": find_bin(b.get("name", ""), real_os)})
        if ok:
            lines.append(f"[ок] {b.get('name')} на месте.")
        else:
            lines.append(f"- Нет {b.get('name')} — при необходимости: {hint}.")

    # ── особенности ОС ───────────────────────────────────────────────────────
    if report["notes"]:
        lines.append("")
        lines.append(f"Особенности {prof['title']}:")
        lines.extend(f"  — {n}" for n in report["notes"])

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
        print()
        if report["critical_ok"]:
            print("Готово к установке.")
        elif not arch["ok"]:
            # Единственный случай, когда «доустановите и повторите» — вранье:
            # доустанавливать нечего, пакетов под этот процессор не существует.
            print("Установку на этом компьютере продолжать нельзя — причина выше. "
                  "Ставить и повторять нечего.")
        elif report["python"]["switch_interpreter"] and not git_blocking:
            # Ставить нечего — просто не тот интерпретатор запустил установку.
            # Ветку выровняли: на Windows это ровно тот же случай (питон лежит
            # внутри папки завода и не в PATH), и молчать про него было нельзя.
            print("Ставить ничего не надо — установку нужно повторить подходящим "
                  "питоном (путь указан выше).")
        else:
            print("Сначала доустановите критичное выше, потом повторите.")

    return 0 if report["critical_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
