# -*- coding: utf-8 -*-
"""
offline_install — развёртывание зависимостей в `.venv` на машине клиента.

ДВЕ ВЕТКИ ПО ОС — РАЗНЫЕ ПО СУТИ, НЕ ПО МЕЛОЧАМ:

  • **Windows (основная, обкатанная)** — ОФЛАЙН из вендоренного wheelhouse, ниже
    описана подробно. Не меняется: тот же embeddable-Python, тот же `._pth`-приём,
    тот же `pip.ini`. Ветка выбирается ПЕРВОЙ строкой main() и дальше идёт как шла.
  • **macOS/Linux** — ОНЛАЙН с PyPI (`install_posix()` в конце файла). Wheelhouse и
    embeddable-Python тут не нужны и не используются: brew-питон несёт `venv` и
    `ensurepip` штатно, поэтому `.venv` создаётся `venv.EnvBuilder` — так же, как
    это делает `install/make_venv.py`. Принято владельцем: мак-коробка ставится
    из интернета, офлайн-wheelhouse под мак не собираем (нет второй платформы для
    вендоринга и нет подписи Apple — архив и так распаковывается руками).

ДАЛЬШЕ — ПРО WINDOWS-ВЕТКУ.

Запускается ПОСТ-СКРИПТОМ инсталлятора (Inno Setup) на машине клиента СРАЗУ после
распаковки — БЕЗ ИНТЕРНЕТА. Делает четыре вещи:

  1) создаёт `.venv` В КОРНЕ установки КОПИРОВАНИЕМ embeddable-Python (тот же путь
     `.venv/Scripts/python.exe`, чтобы мастер /install и doctor'ы видели привычную
     раскладку). `venv.EnvBuilder`/`ensurepip` тут неприменимы — официальный
     embeddable-дистрибутив python.org модуль `venv` не несёт (обнаружено на реальном
     прогоне: падал `ModuleNotFoundError: No module named 'venv'` на импорте);
  2) bootstrap pip В ЭТОЙ КОПИИ (у python-embed нет pip и site по умолчанию: правим
     `pythonXY._pth` → раскомментируем `import site`, ставим pip zip-трюком прямо из
     `pip-*.whl` в wheelhouse — без ensurepip и без сети);
  3) ставит в `.venv` ВСЕ колёса из wheelhouse с `--no-index --find-links wheelhouse`
     (ноль сети);
  4) кладёт в `.venv` файл `pip.ini` с `no-index=true` + `find-links=<wheelhouse>` —
     чтобы ЛЮБОЙ последующий `pip install` мастера установки тоже шёл офлайн и
     идемпотентно (уже поставленное не тянется из сети). Так офлайн-гарантия держится
     без единой правки движка и мастера.

ЧЕСТНО ПРО ГРАНИЦЫ. Финальная сборка .exe и приёмка распаковки+рендера — только на
Windows (BUILD-RECIPE.md). Здесь — кроссплатформенный рантайм-скрипт; Windows-ветка
на маке проверяется на «сухом» прогоне (--dry-run) и на логике путей.

Запуск (обычно из Inno; можно руками из корня установки):
    python installer/offline_install.py --root .            # офлайн-установка
    python installer/offline_install.py --wheelhouse DIR    # иной wheelhouse (Windows)
    python installer/offline_install.py --embed-dir python  # каталог python-embed (bootstrap pip)
    python installer/offline_install.py --dry-run           # показать план, не ставить

На macOS/Linux те же команды работают, но ставят ОНЛАЙН с PyPI; --wheelhouse и
--embed-dir там не нужны и игнорируются (о чём скрипт честно печатает).

Код возврата: 0 — окружение развёрнуто; 1 — ошибка установки; 2 — плохой ввод.
Только stdlib (+ вызовы pip). Без input(). Без секретов.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ⚠️ `venv` ЗДЕСЬ НЕ ИМПОРТИРОВАТЬ. Windows-ветка работает под embeddable-Python
# из коробки, а в нём модулей `venv`/`ensurepip` НЕТ ВОВСЕ: любой импорт на
# верхнем уровне убивает пост-скрипт инсталлятора целиком, `.venv` не создаётся
# и коробка у клиента мёртвая. Мак-ветке `venv` нужен — она импортирует его
# внутри make_venv_native(), уже после развилки по ОС.


def is_windows() -> bool:
    """Ветка ОС. Определяется ровно так же, как в install/check_env.py."""
    return platform.system().strip().lower() == "windows"


def find_root(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).resolve()
        return p
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "installer" else Path.cwd()


def venv_python(root: Path) -> Path:
    win = root / ".venv" / "Scripts" / "python.exe"
    nix = root / ".venv" / "bin" / "python"
    return win if win.exists() else nix


def enable_embed_site(embed_dir: Path) -> bool:
    """Разрешить `import site` во встраиваемом Python (нужно для pip)."""
    changed = False
    for pth in embed_dir.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8", errors="replace")
        if "\n#import site" in text or text.strip().endswith("#import site"):
            text = text.replace("#import site", "import site")
            pth.write_text(text, encoding="utf-8")
            changed = True
    return changed


def make_venv_from_embed(root: Path, embed_dir: Path) -> Path:
    """Собрать `.venv` копированием embeddable-Python.

    `venv.EnvBuilder`/`ensurepip` тут неприменимы: официальный embeddable-дистрибутив
    python.org НЕ включает модуль `venv` (обнаружено на реальном прогоне — падал
    `ModuleNotFoundError: No module named 'venv'` прямо на импорте). Раскладка
    получается плоской — как у самого embeddable-дистрибутива: `.venv/Scripts/`
    содержит `python.exe` рядом со своими `.dll`/`.zip`, это штатно для такого
    интерпретатора и не мешает коду, которому нужен только `.venv/Scripts/python.exe`.

    ⚠️ КРИТИЧНО (обнаружено на реальном прогоне): наличие `python311._pth` рядом с
    `python.exe` включает у CPython «изолированный» режим путей — в нём `PYTHONPATH`
    ИГНОРИРУЕТСЯ ПОЛНОСТЬЮ, sys.path строится только из строк самого `._pth`. А
    `run_doctors.py` (и, вероятно, другие места) рассчитывают на `PYTHONPATH=modules/
    :root` перед вызовом `python -m <module>.doctor`. Поэтому здесь же дописываем
    абсолютные пути `root` и `root/modules` ПРЯМО В `._pth` — иначе `modules/*`
    невидимы для любого субпроцесса, что бы он ни выставлял в PYTHONPATH.
    """
    venv_dir = root / ".venv"
    scripts = venv_dir / "Scripts"
    if (scripts / "python.exe").exists():
        return venv_dir
    scripts.mkdir(parents=True, exist_ok=True)
    for item in embed_dir.iterdir():
        dst = scripts / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    for pth in scripts.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8", errors="replace")
        extra = f"{root}\n{root / 'modules'}\n"
        if str(root) not in text:
            # добавляем ДО '# Uncomment ...'/'import site', чтобы наши пути точно
            # попали в sys.path независимо от того, включён ли site
            head, sep, tail = text.partition("\n\n")
            pth.write_text(head + "\n" + extra + sep + tail, encoding="utf-8")
    return venv_dir


def bootstrap_pip(py: Path, py_dir: Path, wheelhouse: Path) -> bool:
    """Поставить pip в интерпретатор `py`, у которого его ещё нет (embeddable-Python).

    Приём: `import site` в `_pth` + прямая распаковка `pip-*.whl` в `Lib/site-packages`
    (`zipfile`, stdlib) — без интернета и без ensurepip, которого во встраиваемом
    Python нет. Запуск `python pip.whl/pip install pip` тут не годится: современный
    pip (26.x) отказывается «менять себя» не через `-m pip` («To modify pip, please
    run ... -m pip install»), а `-m pip` при отсутствующем pip недоступен — замкнутый
    круг. Прямая распаковка колеса — ровно то, что pip делает для чистого wheel без
    build-шага, так что результат идентичен.
    """
    site_pkgs = py_dir / "Lib" / "site-packages"
    if (site_pkgs / "pip").exists():
        return True
    enable_embed_site(py_dir)
    pip_whl = next(iter(wheelhouse.glob("pip-*.whl")), None)
    if not pip_whl:
        return False
    site_pkgs.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pip_whl) as zf:
        zf.extractall(site_pkgs)
    return (site_pkgs / "pip").exists()


def stamp_client_build(root: Path) -> Path:
    """Пометить установку как КЛИЕНТСКУЮ сборку → activation.is_enforced() = жёстко.

    В нашем dev-checkout этого файла нет, поэтому разработка идёт в мягком режиме, а
    у клиента гейт активации блокирует рендер/публикацию без валидной лицензии (BOX-29).
    """
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    stamp = cfg / ".client_build"
    stamp.write_text("КонтентЗавод: клиентская сборка. Гейт активации — жёсткий.\n",
                     encoding="utf-8")
    return stamp


def write_pip_ini(venv_dir: Path, wheelhouse: Path) -> Path:
    """pip.ini в .venv: все будущие pip-установки — офлайн из wheelhouse."""
    ini = venv_dir / "pip.ini"                       # Windows читает <venv>/pip.ini
    body = ("[global]\n"
            "no-index = true\n"
            f"find-links = {wheelhouse}\n"
            "[install]\n"
            "no-warn-script-location = true\n")
    ini.write_text(body, encoding="utf-8")
    # для не-Windows (dev-проверка) продублируем как pip.conf
    (venv_dir / "pip.conf").write_text(body, encoding="utf-8")
    return ini


def setup_daily_report(root: Path) -> int:
    """Завести (или снять) суточный отчёт — ПО ОТВЕТУ КЛИЕНТА в мастере.

    Ответ живёт в `daily_report_enabled` (модуль publishing, вопрос идёт сразу
    за настройкой Telegram-архива). Сам ответ ничего не запускает — исполняет
    его этот шаг, `--from-answer`: «да» заводит задание, всё остальное снимает.
    Снимать важно не меньше, чем заводить: клиент, передумавший на повторной
    установке, иначе продолжал бы получать отчёты.

    Беда здесь НЕ роняет установку: без отчёта завод полностью работоспособен.
    """
    helper = Path(__file__).resolve().parent / "schedule_report.py"
    if not helper.is_file():
        return 2
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("kz_schedule_report", helper)
        if spec is None or spec.loader is None:
            return 2
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return int(mod.main(["--from-answer", "--root", str(root)]))
    except Exception as e:                             # noqa: BLE001
        print(f"Отчёт за день завести не удалось ({e}). На работу завода это не "
              f"влияет. Повторить позже: "
              f"python installer/schedule_report.py --from-answer")
        return 1


def setup_publish_schedule(root: Path) -> int:
    """Завести расписание выпуска (агент запуска macOS) и сказать об этом клиенту.

    Вся механика — в installer/schedule_publisher.py, здесь только вызов и печать
    одной человеческой строки. Импортируем по пути, а не через `import`: файл
    лежит рядом, но каталог installer/ не обязан быть пакетом.

    Любая беда здесь НЕ роняет установку: без расписания завод остаётся полностью
    работоспособным — просто выпускать придётся по команде клиента, как раньше.
    Возвращаем код от schedule_publisher (0 / 2 / 1) — на будущее, вызывающему.
    """
    helper = Path(__file__).resolve().parent / "schedule_publisher.py"
    if not helper.is_file():
        return 2
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("kz_schedule_publisher", helper)
        if spec is None or spec.loader is None:
            return 2
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        code = mod.main(["--install", "--root", str(root), "--brief"])
        if code == 0:
            print("Расписание заведено: пока вы в системе, завод сам выпускает "
                  "то, что вы ОДОБРИЛИ. Неодобренное он не трогает — "
                  "утверждённый план одобрением материала не считается. "
                  "Проверить в любой момент:")
            print("  python installer/schedule_publisher.py --status")
        return int(code)
    except Exception as e:                             # noqa: BLE001
        print(f"Расписание выпуска завести не удалось ({e}). Завод от этого не "
              f"ломается: выпуск остаётся по вашей команде. Повторить позже: "
              f"python installer/schedule_publisher.py --install")
        return 1


# ── ВЕТКА macOS / Linux: .venv штатным способом + установка с PyPI ───────────
def make_venv_native(root: Path) -> Path:
    """`.venv` обычным `venv.EnvBuilder` — как в install/make_venv.py.

    На маке весь обходной приём Windows-ветки не нужен: brew-питон (и питон с
    python.org) несут модули `venv` и `ensurepip`, поэтому `python3.N -m venv`
    работает штатно, раскладка получается канонической (`.venv/bin/python`), и
    `._pth`-трюка с игнорированием PYTHONPATH тут не возникает в принципе.

    Импорт `venv` — ЛОКАЛЬНЫЙ и намеренно: см. предупреждение у импортов файла.
    """
    import venv

    venv_dir = root / ".venv"
    if not venv_python(root).exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(str(venv_dir))
    return venv_dir


def _site_packages(root: Path) -> Path | None:
    """`.venv/lib/python3.X/site-packages` — версия минора неизвестна заранее."""
    for cand in sorted((root / ".venv" / "lib").glob("python*/site-packages")):
        if cand.is_dir():
            return cand
    return None


def audit_installed_licenses(root: Path) -> None:
    """Лицензионный гейт ПОСЛЕ установки (BOX-27/мак-хвост).

    Windows-ветка сканирует вендоренный wheelhouse ДО того, как коробка уедет
    клиенту (`build_box.py`). На маке вендоринга нет вообще — реальные пакеты
    известны только ПОСЛЕ `pip install` на машине клиента, поэтому проверять
    можно только здесь и только сейчас. `installer/license_audit.py` уже умеет
    сканировать установленный `site-packages` (`--site`) — этим кодом он просто
    вызывается, чем-то новым не является.

    ТОЛЬКО ПРЕДУПРЕЖДЕНИЕ, установку не останавливает: то же решение владельца
    по BOX-27 («GPL — не блокер запуска»), теперь явно распространённое на мак,
    а не тихое предположение «лицензии от платформы не зависят» (оно неверно —
    проверено: av==15.1.0 бандлит libx264/libx265 в .dylibs/ так же, как в
    Windows-колесе). Ошибка самого аудита (нет site-packages, скрипт не нашёлся)
    тоже не останавливает установку — это диагностика, а не гейт."""
    site = _site_packages(root)
    audit = Path(__file__).resolve().parent / "license_audit.py"
    if site is None or not audit.is_file():
        return
    try:
        r = subprocess.run([sys.executable, str(audit), "--site", str(site)],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return
    if r.returncode == 1:
        print("\n⚠ Лицензионная проверка нашла GPL/AGPL среди поставленных пакетов "
              "(подробности — installer/license_audit.py --site .venv/lib/python*/"
              "site-packages). Это тот же класс находки, что и на Windows (BOX-27): "
              "решение владельца — не блокер запуска, зафиксировать и вернуться позже.")
    elif r.returncode not in (0, 2):
        print("\ni Лицензионная проверка не смогла отработать (не критично, "
              "установка продолжается).")


def find_lock(root: Path) -> Path | None:
    """Список пакетов для онлайн-установки. Порядок — от «того же, что у Windows»
    к запасным вариантам. Ничего не выдумываем: версии берём готовые, все с '=='."""
    for cand in (root / "installer" / "vendor" / "requirements.lock.txt",
                 root / "installer" / "vendor" / "requirements.full.lock",
                 root / "requirements.lock.txt"):
        if cand.is_file():
            return cand
    return None


def install_posix(root: Path, args) -> int:
    """macOS/Linux: `.venv` штатным venv + `pip install` с PyPI (нужен интернет).

    Офлайн-wheelhouse под мак НЕ собираем — решение владельца. Поэтому здесь нет
    ни `--no-index`, ни `pip.ini` с `find-links`: закрепить офлайн-режим означало бы
    сломать клиенту любую последующую доустановку.
    """
    lock = find_lock(root)
    title = "macOS" if platform.system().strip().lower() == "darwin" else platform.system()
    print(f"Корень установки: {root}")
    print(f"Режим: ОНЛАЙН с PyPI ({title}); wheelhouse и embeddable-Python не нужны.")
    if args.wheelhouse or args.embed_dir:
        print("(--wheelhouse/--embed-dir на этой ОС не используются — они только "
              "для офлайн-ветки Windows.)")
    if lock is None:
        print("Не найден список пакетов (installer/vendor/requirements.lock.txt, "
              "requirements.full.lock или requirements.lock.txt в корне). "
              "Поэтапная установка ставит зависимости сама: "
              "python install/make_venv.py --plan install/.plan.json")
        return 2
    print(f"Список пакетов: {lock.relative_to(root) if lock.is_relative_to(root) else lock}")

    if args.dry_run:
        print("\nПлан (dry-run):")
        print(f"  1) создать {root/'.venv'} штатным venv (python -m venv)")
        print(f"  2) обновить pip в {root/'.venv'/'bin'/'python'}")
        print(f"  3) pip install -r {lock.name}   (с PyPI, нужен интернет)")
        print("  4) пометить клиентскую сборку (config/.client_build)")
        return 0

    if not venv_python(root).exists():
        print("Создаю локальное окружение .venv …")
        try:
            make_venv_native(root)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Не удалось создать .venv: {e}")
            return 1
    py = venv_python(root)
    if not py.exists():
        print("Не удалось создать .venv — окружение не собралось.")
        return 1

    print("Обновляю pip …")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=False)

    print("Ставлю зависимости с PyPI (это может занять время) …")
    r = subprocess.run([str(py), "-m", "pip", "install", "--no-input", "-r", str(lock)],
                       check=False)
    if r.returncode != 0:
        print("\nУстановка части пакетов не удалась. Частые причины на маке: нет "
              "интернета, или под этот питон ещё нет готовых колёс (тяжёлые пакеты "
              "вроде mediapipe/onnxruntime). Проверьте интернет и повторите — "
              "уже поставленное не переустановится. Версию питона проверяет "
              "install/check_env.py (окно версий считается из манифестов).")
        return 1

    audit_installed_licenses(root)

    stamp = stamp_client_build(root)
    print(f"Готово: окружение развёрнуто. Отмечена клиентская сборка ({stamp.name}) — "
          "активация обязательна перед рендером/публикацией.")

    # Расписание выпуска. На маке коробка ставится РАСПАКОВКОЙ АРХИВА — установщика,
    # который завёл бы агент запуска, здесь нет вообще. Поэтому шаг живёт тут: это
    # единственное место, через которое проходит любая мак-установка.
    # На Windows этот же шаг делает секция [Run] в installer/kontent-zavod.iss —
    # дублировать его здесь нельзя, иначе задание заводилось бы дважды.
    setup_publish_schedule(root)
    setup_daily_report(root)

    print("Первый шаг мастера — активация: python installer/activation.py --check license.key")
    return 0


def main(argv=None) -> int:
    # Описание/флаги НЕ трогаем — на Windows `--help` должен остаться прежним.
    ap = argparse.ArgumentParser(description="Офлайн-развёртывание зависимостей из wheelhouse.")
    ap.add_argument("--root", help="Корень установки (по умолчанию — авто-поиск).")
    ap.add_argument("--wheelhouse", help="Каталог с колёсами (по умолчанию installer/vendor/wheels).")
    ap.add_argument("--embed-dir", help="Каталог embeddable-Python — источник для копирования "
                                        "в .venv (по умолчанию <root>/python).")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = find_root(args.root)

    # ── РАЗВИЛКА ПО ОС. Всё, что ниже этой строки, — нетронутая Windows-ветка.
    if not is_windows():
        return install_posix(root, args)

    wheelhouse = Path(args.wheelhouse) if args.wheelhouse else root / "installer" / "vendor" / "wheels"
    lock = root / "installer" / "vendor" / "requirements.lock.txt"

    if not wheelhouse.is_dir() or not any(wheelhouse.glob("*.whl")):
        print(f"Нет колёс в wheelhouse: {wheelhouse}. Сначала вендоринг "
              f"(installer/fetch_wheels.py) на этапе подготовки коробки.")
        return 2

    wheels = sorted(wheelhouse.glob("*.whl"))
    print(f"Корень установки: {root}")
    print(f"Wheelhouse: {wheelhouse} (колёс: {len(wheels)})")
    print("Режим: ОФЛАЙН (--no-index --find-links), сеть не используется.")

    embed_dir = Path(args.embed_dir) if args.embed_dir else root / "python"

    if args.dry_run:
        print("\nПлан (dry-run):")
        print(f"  1) скопировать embeddable-Python {embed_dir} → {root/'.venv'/'Scripts'}")
        print("  2) bootstrap pip в копии (venv-модуля во встраиваемом Python нет)")
        print(f"  3) pip install --no-index --find-links {wheelhouse} -r {lock.name}")
        print(f"  4) записать .venv/pip.ini (офлайн по умолчанию)")
        return 0

    if not (embed_dir / "python.exe").exists():
        print(f"Не найден embeddable-Python в {embed_dir} (--embed-dir) — нечем "
              f"наполнить .venv.")
        return 1

    # 1) .venv в корне — копия embeddable-Python (venv.EnvBuilder тут неприменим:
    #    в python.org embeddable-дистрибутиве модуля venv/ensurepip нет)
    if not venv_python(root).exists():
        print("Создаю локальное окружение .venv (копия embeddable-Python) …")
        make_venv_from_embed(root, embed_dir)
    py = venv_python(root)
    if not py.exists():
        print("Не удалось создать .venv."); return 1

    # 2) bootstrap pip в .venv (zip-трюк: пакет pip запускается прямо из .whl)
    if not bootstrap_pip(py, py.parent, wheelhouse):
        print("Не удалось поставить pip в .venv (нет pip-*.whl в wheelhouse?).")
        return 1

    # 3) офлайн-установка всех колёс
    print("Ставлю зависимости из wheelhouse ОФЛАЙН (может занять время) …")
    cmd = [str(py), "-m", "pip", "install", "--no-input",
           "--no-index", "--find-links", str(wheelhouse)]
    if lock.is_file():
        cmd += ["-r", str(lock)]
    else:
        cmd += [str(w) for w in wheels]
    r = subprocess.run(cmd, check=False)
    if r.returncode != 0:
        print("\nОфлайн-установка не завершилась. Проверь, что wheelhouse содержит "
              "колёса под этот Python/ABI (см. installer/manifest.json).")
        return 1

    # 4) закрепить офлайн-режим для будущих pip-вызовов мастера
    ini = write_pip_ini(root / ".venv", wheelhouse)
    print(f"Готово: окружение развёрнуто офлайн. Офлайн-режим закреплён: {ini.name}.")

    # 5) пометить клиентскую сборку → жёсткий гейт активации (BOX-29)
    stamp = stamp_client_build(root)
    print(f"Отмечена клиентская сборка ({stamp.name}) — активация обязательна перед "
          "рендером/публикацией.")
    print("Теперь мастер /install дойдёт до шагов конфига/папок без обращения к сети.")
    print("Первый шаг мастера — активация: python installer/activation.py --check license.key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
