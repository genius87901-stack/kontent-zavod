# -*- coding: utf-8 -*-
"""
doctor модуля niche-hooks — проверка готовности виральной разведки ниши.

Стандарт вызова (CONTRACT.md §2): python -m niche_hooks.doctor из корня установки,
modules/ на PYTHONPATH. Код возврата 0 — инструмент полностью готов; 2 — часть
источников недоступна (урезанный режим); 1 — сама проверка не смогла отработать.
На stdout по-русски человеческим языком, что не так и как починить. НЕ стектрейс.

Что проверяем (см. skills/niche-hooks/SKILL.md — три слоя: метрики → разбор → смыслы):
  • vidIQ MCP — источник метрик (аутлаеры) и разбора Instagram-рилсов. Программно
    из Python недоступен (это MCP-сервер внутри Claude Code), поэтому проверить
    нельзя — только напоминаем: он должен быть подключён, и он тратит кредиты.
  • yt-dlp + ffmpeg — нужны скиллу /watch для разбора YouTube/TikTok (скачать ролик,
    нарезать первые кадры). Без них разбор IG через vidIQ всё равно работает.
  • Скилл /watch (плагин claude-video) — разбор YouTube/TikTok. Внешний плагин
    Claude Code, мы его не ставим — только проверяем наличие.

Только stdlib. Кроссплатформенно (Windows — целевая ОС клиента). Без абсолютных путей.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


# ── «чем доставить» под ТЕКУЩУЮ ОС (BOX-18) ──────────────────────────────────
# На маке команды winget не существует, и клиент упирался в тупиковую подсказку.
# ОС определяем ЕДИНЫМ принятым в проекте способом — install/check_env.py
# (detect_os + manifest_hint + ключи <os>_install из module.manifest.json).
# Своего определения ОС не заводим.
# Windows-путь не меняется: там возвращается windows_install из манифеста, а он
# совпадает с текстом, что был зашит здесь раньше; при любой неудаче — прежний текст.
# Для ffmpeg записи в манифесте niche-hooks нет (её и не должно быть: ffmpeg
# необязателен и приезжает пакетом imageio-ffmpeg) — тогда на маке подставляется
# generic-подсказка профиля macOS из check_env: `brew install ffmpeg`.
# Раз он приезжает пакетом — там его и ищем в первую очередь (см. _ffmpeg_where):
# искать только в PATH значило бы каждый раз объявлять здоровую коробку урезанной.
_CHECK_ENV_CACHE: list = []      # [] — ещё не грузили; [mod|None] — загружено


def _check_env_module():
    """install/check_env.py как модуль (он не пакет и не лежит на PYTHONPATH)."""
    if _CHECK_ENV_CACHE:
        return _CHECK_ENV_CACHE[0]
    mod = None
    try:
        import importlib.util
        for parent in Path(__file__).resolve().parents:
            cand = parent / "install" / "check_env.py"
            if cand.is_file():
                spec = importlib.util.spec_from_file_location("factory_check_env", cand)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                break
    except Exception:
        mod = None
    _CHECK_ENV_CACHE.append(mod)
    return mod


def _manifest_binary(name: str):
    """Запись system_binaries своего модуля по имени бинарника (или None)."""
    try:
        man = Path(__file__).resolve().parent / "module.manifest.json"
        data = json.loads(man.read_text(encoding="utf-8-sig"))
        for b in data.get("system_binaries", []) or []:
            if (b.get("name") or "").strip().lower() == name:
                return dict(b)
    except Exception:
        pass
    return None


def _install_hint(name: str, windows_hint: str) -> str:
    """Команда установки `name` для текущей ОС: winget / brew / apt."""
    ce = _check_env_module()
    if ce is None:
        return windows_hint
    try:
        os_key = ce.detect_os()
        entry = _manifest_binary(name) or {"name": name}
        if os_key == "windows":
            return (entry.get("windows_install") or "").strip() or windows_hint
        return ce.manifest_hint(entry, os_key)
    except Exception:
        return windows_hint


def _ffmpeg_where() -> str:
    """Где на этой машине лежит ffmpeg: "system" / "pip" / "" (нигде).

    ЗАЧЕМ ОТДЕЛЬНО ОТ _bin_ok. Проверка через PATH (`shutil.which`) на коробке
    отвечает «нет» ВСЕГДА: системный ffmpeg туда не ставится, завод рендерит
    бинарём из пакета imageio-ffmpeg. Из-за этого строка «Нет системного ffmpeg»
    стояла в 100% отчётов со всех машин, включая полностью здоровые, и в сводку
    владельцу каждый раз уезжала категория `no_ffmpeg`. Строка, которая всегда
    красная, перестаёт читаться — и настоящую беду в ней уже не разглядеть.
    Порядок проверки тот же, что у publishing/doctor.py::_ffmpeg_ok: сначала
    штатный путь завода (пакет), потом системный.
    """
    try:
        import imageio_ffmpeg
        if Path(imageio_ffmpeg.get_ffmpeg_exe()).exists():
            return "pip"
    except Exception:          # пакета нет или бинарь не распаковался
        pass
    if _bin_ok("ffmpeg", "ffmpeg -version"):
        return "system"
    return ""


def _bin_ok(name: str, check_cmd: str = "") -> bool:
    if shutil.which(name):
        return True
    if not check_cmd:
        return False
    try:
        r = subprocess.run(check_cmd, shell=True, capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _watch_skill_present() -> bool:
    """Ищем скилл /watch (плагин claude-video) в домашних плагинах Claude Code.

    Path.home() вычисляется в рантайме, это не литеральный абсолютный путь —
    запрет CONTRACT.md §3 на захардкоженные пользовательские пути не нарушается.
    """
    base = Path.home() / ".claude" / "plugins"
    if not base.exists():
        return False
    # у разных установок путь до скилла немного разный — ищем watch.py под plugins/.
    for cand in base.rglob("skills/watch/scripts/watch.py"):
        if cand.is_file():
            return True
    for cand in base.rglob("watch/scripts/watch.py"):
        if cand.is_file():
            return True
    return False


def main() -> int:
    lines: list[str] = []
    missing: list[str] = []

    yt_ok = _bin_ok("yt-dlp", "yt-dlp --version")
    ff_where = _ffmpeg_where()
    watch_ok = _watch_skill_present()

    if yt_ok:
        lines.append("✓ yt-dlp на месте — можно скачивать ролики YouTube/TikTok для разбора.")
    else:
        missing.append("yt-dlp")
        lines.append("• Нет yt-dlp — разбор YouTube/TikTok будет недоступен. "
                     "Поставить: %s. "
                     "Разведка Instagram через vidIQ работает и без него."
                     % _install_hint("yt-dlp", "winget install yt-dlp.yt-dlp"))
    if ff_where == "system":
        lines.append("✓ ffmpeg на месте (системный) — кадры из роликов нарезаются.")
    elif ff_where == "pip":
        # НЕ пишем в missing: ffmpeg на машине есть, и это тот самый бинарь,
        # которым завод рендерит. Оговорку про PATH говорим вслух — не молчим и
        # не выдаём её за поломку.
        lines.append("✓ ffmpeg на месте — бинарь из пакета imageio-ffmpeg, тот же, "
                     "которым завод рендерит ролики. В PATH его нет: если /watch "
                     "не найдёт ffmpeg сам, укажите ему путь ключом "
                     "--ffmpeg-location. Отдельно ставить ничего не нужно.")
    else:
        missing.append("ffmpeg")
        lines.append("• ffmpeg не найден ни пакетом, ни в системе — для разбора "
                     "YouTube/TikTok нужен он (нарезка первых кадров). Поставить: "
                     "pip install imageio-ffmpeg==0.6.0 (либо системный: %s)."
                     % _install_hint("ffmpeg", "winget install Gyan.FFmpeg"))
    if watch_ok:
        lines.append("✓ Скилл /watch найден — разбор YouTube/TikTok подключён.")
    else:
        missing.append("/watch")
        lines.append("• Скилл /watch (плагин claude-video) не найден — разбор "
                     "YouTube/TikTok будет недоступен. Разведка Instagram через vidIQ "
                     "работает и без него.")

    # vidIQ MCP — программно не проверяется, только напоминание (главный источник).
    lines.append("")
    lines.append("ВАЖНО про виральную разведку (niche-hooks):")
    lines.append("  — Это РУЧНОЙ инструмент разведки конкурентов, он НЕ входит в "
                 "авто-пайплайн и запускается по вашей команде.")
    lines.append("  — Главный источник данных — vidIQ (подключается коннектором в самом "
                 "Claude). Этот скрипт коннекторы не видит — проверить может агент: "
                 "скажите ему «проверь, подключён ли vidIQ», он покажет остаток кредитов.")
    lines.append("  — Регистрация на vidiq.com и подключение коннектора в Claude — ДВА "
                 "разных действия. Одного входа на сайт мало. Пошагово — в файле "
                 "«ПОДКЛЮЧЕНИЕ_VIDIQ.md» в корне завода.")
    lines.append("  — vidIQ работает на ВАШЕМ личном аккаунте vidIQ и ВАШИХ кредитах — "
                 "это отдельная платная регистрация, которую вы заводите сами. Наши "
                 "ключи в коробку не входят; деньги за разведку списываются с вашего счёта.")
    lines.append("  — Разведка ТРАТИТ КРЕДИТЫ vidIQ. Скилл всегда сначала показывает "
                 "остаток и смету и не запускает дорогой сбор без вашего «да».")

    print("\n".join(lines))

    if missing:
        print()
        print("Итог: базовая разведка (Instagram через vidIQ) доступна, но разбор "
              "YouTube/TikTok — в урезанном режиме, не хватает: " + ", ".join(missing) + ".")
        # BOX-51: именно 2. Разведка — РУЧНОЙ инструмент, она не входит в
        # автопайплайн; отсутствие системного ffmpeg или yt-dlp гасит только
        # разбор чужих роликов и не мешает заводу печь контент. Кодом 1 это
        # уходило владельцу красной тревогой «коробка сломана».
        return 2
    print()
    print("Итог: виральная разведка готова (при подключённом vidIQ MCP).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
