# -*- coding: utf-8 -*-
"""
zavod_multi — пульт маркетолога: несколько заводов, по одному на клиента.

ЗАЧЕМ. Маркетолог ведёт несколько своих клиентов параллельно и не должен путать
их данные. Он не технарь: Windows, никаких git-команд руками. Всё, что он
делает, — говорит помощнику «заведи мне нового клиента».

АРХИТЕКТУРА, КОТОРАЯ НЕ ОБСУЖДАЕТСЯ:
    один клиент = один репозиторий = одна папка = одно окно.
Изоляция — это граница ПАПКИ. «Активного клиента» в настройках нет: переключать
нечего, значит и перепутать нечего. Всё, что здесь есть, — заведение новых
папок и обход существующих; никакого состояния «с кем я сейчас работаю».

НИ git, НИ gh НЕ НУЖНЫ. Раньше до первого клиента человек ставил две программы,
перезапускал окно и проходил отдельный вход `gh auth login` с выбором трёх
пунктов меню — пять шагов настройки ПК до первого полезного действия. Теперь всё
делают обычные запросы к GitHub из `github_backup.py`, а вход один: код
устройства, как у телевизора.

ЧТО УМЕЕТ:
    python zavod_multi.py setup                    # пульт: вход, папка заводов
    python zavod_multi.py new "Елена Сокольская"   # завести завод клиенту
    python zavod_multi.py new "Имя" --dry-run      # показать шаги, ничего не делать
    python zavod_multi.py update                   # обновить движок в одном заводе
    python zavod_multi.py update --all             # ...во всех заводах
    python zavod_multi.py list                     # какие заводы есть
    python zavod_multi.py slug "Елена Сокольская"  # проверить транслитерацию

🔴 ПРЕДОХРАНИТЕЛЬ. Ни одна команда здесь не публикует и не умеет публиковать:
`autopost`, `vkpost`, `smmbox_post`, `story_publisher` не зовутся, флага
`--send` в этом файле нет ни разу. Это проверяется тестом grep'ом по файлу, а не
обещанием в комментарии.

Только stdlib. Без input() в шагах, которые могут выполняться из хука.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 🔴 НИ gh, НИ git ПУЛЬТУ БОЛЬШЕ НЕ НУЖНЫ (упрощение 02.09.2026).
# Первая редакция звала `gh repo create --template` и `git clone`. Замер
# сложности показал, что до первого клиента человек должен был поставить ДВЕ
# программы, перезапустить окно и пройти отдельный вход `gh auth login` с
# выбором трёх пунктов меню — то есть пять шагов настройки ПК до того, как
# случится хоть что-то полезное. Для человека, который никогда не настраивал
# компьютер, это не «неудобно», это стена: он не доходит.
# Всё то же самое делают обычные запросы к GitHub, которые уже написаны и
# проверены в `github_backup.py`: репозиторий из шаблона и выкладка файлов на
# диск. Вход остаётся ОДИН — тот же код устройства, что и у резервной копии.
sys.path.insert(0, str(HERE))
import github_backup as gb          # noqa: E402  (после правки sys.path)

MARKETER_CONFIG = Path.home() / ".zavod" / "marketer.config.json"
DEFAULT_FACTORIES_DIR = Path.home() / "Заводы"

#: Ветка движка, из которой приходят обновления.
UPSTREAM_BRANCH = "main"

# ── Транслитерация ───────────────────────────────────────────────────────────
#
# Своя таблица, без зависимостей: правило проекта — только stdlib (офлайн-
# установщик возит колёса, и каждая новая зависимость это мегабайты в коробке).
# Таблица по ГОСТ-подобной практике, читаемая человеком: щ→sch, ж→zh, ё→e.
_ТАБЛИЦА = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Украинские и белорусские буквы — маркетолог может вести клиента с таким
    # именем, и «Ї» не должна превращаться в пустую строку молча.
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
}


def slugify(name: str) -> str:
    """«Елена Сокольская» → «elena-sokolskaya». Пустое имя — отказ, не пустой slug.

    Slug становится именем ПАПКИ и именем РЕПОЗИТОРИЯ, поэтому он обязан быть
    безопасным для обоих: только латиница, цифры и дефис. Пустой результат
    вернуть нельзя — папка без имени сложилась бы в корень заводов и смешала бы
    клиентов, а это ровно то, чего вся затея избегает.
    """
    низ = (name or "").strip().lower()
    буквы = []
    for ch in низ:
        if ch in _ТАБЛИЦА:
            буквы.append(_ТАБЛИЦА[ch])
        elif ch.isascii() and (ch.isalnum()):
            буквы.append(ch)
        elif ch in " -_./\\":
            буквы.append("-")
        # всё прочее (эмодзи, знаки препинания, иероглифы) просто выпадает
    slug = "".join(буквы)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError(
            f"из имени «{name}» не получается имя папки латиницей. "
            "Задай короткое имя латиницей вручную: --slug elena")
    return slug


def unique_slug(base: str, root: Path) -> str:
    """Свободное имя папки. Столкновение — не ошибка, а обычная жизнь: двух
    Елен Сокольских у маркетолога может быть две, и вторая не должна лечь
    поверх первой."""
    if not (root / base).exists():
        return base
    for n in range(2, 100):
        кандидат = f"{base}-{n}"
        if not (root / кандидат).exists():
            return кандидат
    raise ValueError(f"не нашлось свободного имени для «{base}» — их уже под сотню")


# ── Настройки маркетолога ────────────────────────────────────────────────────


def load_marketer() -> dict:
    try:
        data = json.loads(MARKETER_CONFIG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_marketer(cfg: dict) -> None:
    MARKETER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    MARKETER_CONFIG.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def factories_dir() -> Path:
    cfg = load_marketer()
    return Path(cfg.get("factories_dir") or DEFAULT_FACTORIES_DIR)


# ── Внешние команды ──────────────────────────────────────────────────────────


def run(cmd: list, cwd: Path = None, dry: bool = False, check: bool = True) -> tuple:
    """(код, вывод). В сухом режиме команда ПЕЧАТАЕТСЯ и не выполняется.

    Сухой режим здесь не удобство, а честность: `gh repo create` и `git push`
    требуют сети и входа, и на машине разработки их проверить нечем. Пусть
    человек видит команду дословно, а не верит на слово.
    """
    показ = " ".join(str(c) for c in cmd)
    if dry:
        print(f"    [сухой режим] {показ}")
        return 0, ""
    try:
        res = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    except FileNotFoundError:
        if check:
            raise SystemExit(f"❌ Нет программы «{cmd[0]}». Запусти: "
                             f"python {Path(__file__).name} setup")
        return 127, ""
    if check and res.returncode != 0:
        хвост = (res.stdout or "") + (res.stderr or "")
        raise SystemExit(f"❌ Не выполнилось: {показ}\n{хвост.strip()[:1200]}")
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def have(program: str) -> bool:
    return shutil.which(program) is not None


# ── setup ────────────────────────────────────────────────────────────────────


GLOBAL_COMMANDS_DIR = Path.home() / ".claude" / "commands"
COMMANDS_SOURCE = "marketer_commands"


def install_commands(src: Path = None, dest: Path = None) -> list:
    """Поставить команды пульта в глобальные команды Claude Code.

    Возвращает имена поставленных команд. Идемпотентно: повтор просто кладёт
    свежие версии поверх. Нечего ставить — пустой список, а не отказ: пульт
    может быть запущен из уже установленной копии.
    """
    src = src or (HERE / COMMANDS_SOURCE)
    dest = dest or GLOBAL_COMMANDS_DIR
    if not src.is_dir():
        return []
    dest.mkdir(parents=True, exist_ok=True)
    поставлено = []
    for файл in sorted(src.glob("*.md")):
        shutil.copy2(файл, dest / файл.name)
        поставлено.append(файл.stem)
    return поставлено


def cmd_setup(args) -> int:
    """Разовая настройка пульта. Ничего не ломает при повторе."""
    print("Настройка пульта маркетолога")
    print()
    беды = []

    # Вход в GitHub. Никаких установок программ: тот же код устройства, что и у
    # резервной копии. Токен лежит в домашней папке пульта, а не в заводе, —
    # пульт один, заводов много.
    token = gb.get_token(HERE)
    if not token:
        print("  Нужен вход в ваш GitHub. Аккаунта нет — заведите бесплатно,")
        print("  нужна только почта: https://github.com/signup")
        print()
        try:
            token = gb.login_device()
            gb.save_token(token, HERE)
        except gb.GitHubError as exc:
            беды.append(f"  🔴 Войти не удалось: {exc}")
            token = None
    if token:
        try:
            me = gb.api("/user", token)
            имя = str(me.get("login", "")) if isinstance(me, dict) else ""
            print(f"  ✔ GitHub подключён{': ' + имя if имя else ''}")
            if имя:
                cfg = load_marketer()
                cfg["github_account"] = имя
                save_marketer(cfg)
        except gb.GitHubError as exc:
            беды.append(f"  🔴 GitHub не отвечает: {exc}")

    # Команды пульта кладём в ГЛОБАЛЬНЫЕ команды Claude Code. Иначе получается
    # задача первого запуска: `/new-client` нужна ДО того, как появился первый
    # завод, а внутри завода её взять неоткуда — заводов ещё нет.
    поставлено = install_commands()
    if поставлено:
        print(f"  ✔ Команды пульта: {', '.join('/' + c for c in поставлено)}")

    корень = Path(args.dir).expanduser() if args.dir else factories_dir()
    корень.mkdir(parents=True, exist_ok=True)
    cfg = load_marketer()
    cfg["factories_dir"] = str(корень)
    if args.template:
        cfg["template_repo"] = args.template
    # ⚠️ Поймано живым прогоном 02.09.2026: флаг `--engine` был объявлен и НИГДЕ
    # не сохранялся, поэтому `new` каждый раз говорил «репозиторий движка не
    # задан» — при том что человек его только что задал. Отказ, выглядящий как
    # успех настройки: setup отчитывался «готово».
    if args.engine:
        cfg["engine_repo"] = args.engine
    cfg.setdefault("created", time.strftime("%Y-%m-%d"))
    save_marketer(cfg)
    print(f"  ✔ Папка заводов: {корень}")
    print(f"  ✔ Настройки:     {MARKETER_CONFIG}")

    if not cfg.get("template_repo"):
        беды.append(
            "  ⚠️ Не задан репозиторий-шаблон, из которого делаются заводы.\n"
            "     Его даёт разработчик завода. Как получишь — выполни:\n"
            f"     python {Path(__file__).name} setup --template <владелец>/<репозиторий>")

    print()
    if беды:
        print("Осталось сделать:")
        for b in беды:
            print(b)
        print()
        print("Как сделаешь — запусти эту же команду ещё раз, она проверит.")
        return 1
    print("Готово. Дальше: «заведи мне нового клиента».")
    return 0


# ── new ──────────────────────────────────────────────────────────────────────


HANDOFF_ШАБЛОН = """# Состояние завода: {имя}

Файл ведёт помощник командой `/handoff`. Руками править не нужно, но читать
можно и нужно — это память между сессиями.

## Кто клиент

{имя}. Ниша, площадки и особенности — по мере того, как выяснятся.

## Что сделано

- {дата} — завод заведён. Смыслы ещё не распакованы.

## Что дальше

1. Распаковка смыслов: собрать `data/Книга_смыслов.md` (11 разделов).
2. Указать папку Google Диска, чтобы завод сам забирал материалы.
3. Подключить площадки клиента.

## Открытые вопросы

- (пока нет)
"""

КНИГА_ШАБЛОН = """# Книга смыслов: {имя}

Это фундамент завода. Пока она пустая, завод соберёт красивые карусели ни о чём.

Заполняется на созвоне или помощником по расшифровке созвона: скажи ему
«проведи со мной распаковку» и дай транскрибацию.

## 1. Кто клиент и чем занимается

## 2. Для кого он работает — портрет аудитории

## 3. Какую боль он снимает

## 4. Чем отличается от других

## 5. Как он говорит — голос и словарь

## 6. О чём говорить можно, о чём нельзя

## 7. Опорные истории и примеры

## 8. Продукты и что человек получает

## 9. Возражения и ответы на них

## 10. Призывы к действию

## 11. Площадки и как на них себя ведём
"""


def compose_claude_md(папка: Path, имя: str, slug: str) -> None:
    """Пин клиента + обычная инструкция завода, в одном CLAUDE.md.

    Пин идёт ПЕРВЫМ и отдельным файлом-заготовкой (`CLAUDE.pin.md`), а не
    вписывается в текст инструкции: инструкцию переписывает обновление движка,
    и пин при этом обязан уцелеть. Сборка их склеивает при заведении завода.
    """
    пин_файл = папка / "CLAUDE.pin.md"
    основа = папка / "CLAUDE.md"
    if not пин_файл.exists():
        # 🔴 Молчать здесь нельзя. Пин — главный предохранитель от смешения
        # клиентов, и завод без пина выглядит совершенно нормальным: папка есть,
        # движок есть, данные есть. Обнаружится пропажа в тот день, когда
        # помощник в окне «Елены» возьмётся за материалы «Петра».
        # Поймано живым прогоном 02.09.2026: шаблон был собран до того, как
        # заготовка пина появилась, и завод молча вышел без него.
        raise SystemExit(
            "\n❌ ЗАВОД НЕ ЗАВЕДЁН: в шаблоне нет CLAUDE.pin.md — заготовки пина\n"
            "   клиента. Без пина помощник не знает, чей это завод, и заводы\n"
            "   можно перепутать. Это не мелочь, поэтому останавливаюсь.\n"
            "   Чинится пересборкой шаблона: python build_template.py --out ... --force")
    пин = пин_файл.read_text(encoding="utf-8")
    пин = пин.replace("{ИМЯ}", имя).replace("{SLUG}", slug)
    пин_файл.unlink()
    старое = основа.read_text(encoding="utf-8") if основа.exists() else ""
    основа.write_text(пин + старое, encoding="utf-8")
    # Проверяем результат, а не намерение: имя клиента обязано физически лежать
    # в первых строках файла, который агент читает первым.
    голова = основа.read_text(encoding="utf-8")[:2000]
    if имя not in голова:
        raise SystemExit(
            f"\n❌ ЗАВОД НЕ ЗАВЕДЁН: имя «{имя}» не попало в начало CLAUDE.md.\n"
            "   Пин не сработал — заводить завод без пина нельзя.")


def cmd_new(args) -> int:
    имя = args.name
    if not имя:
        print("Как зовут клиента? Повтори команду с именем, например:")
        print('  python zavod_multi.py new "Елена Сокольская"')
        return 2

    cfg = load_marketer()
    корень = factories_dir()
    корень.mkdir(parents=True, exist_ok=True)

    slug = args.slug or unique_slug(slugify(имя), корень)
    папка = корень / slug
    репо = f"factory-{slug}"
    шаблон = args.template or cfg.get("template_repo") or ""
    движок = args.engine or cfg.get("engine_repo") or ""

    print(f"Завожу завод клиента «{имя}»")
    print(f"  папка:       {папка}")
    print(f"  репозиторий: {репо} (приватный)")
    print()

    if папка.exists() and any(папка.iterdir()):
        print(f"❌ Папка {папка} уже есть и не пуста — это чужой завод.")
        print("   Заводить поверх не буду: так и смешиваются клиенты.")
        return 3

    # 1. Репозиторий из шаблона.
    # Шаблон бывает двух видов, и различаются они сами собой: путь на диске —
    # это локальная сборка (так проверяется весь поток без сети и без входа в
    # GitHub), строка вида «владелец/репо» — настоящий шаблон на GitHub.
    локальный = bool(шаблон) and Path(шаблон).expanduser().is_dir()
    адрес = ""
    if локальный:
        # Локальная папка-шаблон: так весь поток проверяется без сети и без
        # входа в GitHub. Копии на GitHub при этом НЕТ, и об этом говорится
        # вслух — иначе «отказ, выглядящий как успех».
        print(f"  1. Беру локальный шаблон {шаблон}…")
        if not args.dry_run:
            shutil.copytree(Path(шаблон).expanduser(), папка,
                            ignore=shutil.ignore_patterns(".git"))
            print("     ⚠️ Шаблон локальный: репозитория на GitHub у этого завода НЕТ.")
    elif шаблон:
        print("  1. Завожу приватный репозиторий из шаблона…")
        if args.dry_run:
            print(f"    [сухой режим] POST /repos/{шаблон}/generate -> {репо} (приватный)")
        else:
            токен = gb.get_token(HERE)
            if not токен:
                print("❌ Сначала настройте пульт: python zavod_multi.py setup")
                return 3
            инфо = gb.generate_from_template(токен, шаблон, репо)
            адрес = f"https://github.com/{инфо['owner']}/{инфо['repo']}"
            print(f"     ✔ {адрес}")
            print("     Выкладываю файлы завода на диск…")
            # GitHub заводит репозиторий из шаблона не мгновенно; первые секунды
            # дерево может быть пустым. Молча получить пустую папку нельзя —
            # человек решит, что завод есть.
            файлов = 0
            for попытка in range(6):
                файлов = gb.materialize(токен, инфо["owner"], инфо["repo"], папка)
                if файлов:
                    break
                time.sleep(3)
            if not файлов:
                print("❌ Репозиторий заведён, но файлы из него не приехали.")
                print(f"   Посмотрите {адрес} — возможно, шаблон пуст.")
                return 3
            print(f"     ✔ Файлов на диске: {файлов}")
    else:
        print("  1. ⚠️ Шаблон не задан — делаю пустую папку завода.")
        print("     Репозитория на GitHub не будет, пока не задан шаблон.")
        папка.mkdir(parents=True, exist_ok=True)

    папка.mkdir(parents=True, exist_ok=True)

    # 2. Данные клиента.
    print("  2. Раскладываю данные клиента…")
    data = папка / "data"
    for под in ("Загрузить", "Готово", "Опубликовано", "НЕ_ТРОГАТЬ", "config"):
        (data / под).mkdir(parents=True, exist_ok=True)
    сегодня = time.strftime("%Y-%m-%d")
    (data / "client.config.json").write_text(json.dumps({
        "name": имя, "slug": slug, "created": сегодня,
        "socials": {"instagram": "", "vk": "", "telegram": "", "youtube": ""},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data / "HANDOFF.md").write_text(
        HANDOFF_ШАБЛОН.format(имя=имя, дата=сегодня), encoding="utf-8")
    книга = data / "Книга_смыслов.md"
    if not книга.exists():
        книга.write_text(КНИГА_ШАБЛОН.format(имя=имя), encoding="utf-8")

    # 3. Пин клиента.
    print("  3. Пишу пин клиента в CLAUDE.md…")
    compose_claude_md(папка, имя, slug)

    # 4. Связь с движком: записываем в настройки завода, а не в remote git.
    # Раньше здесь был `git remote add upstream`, то есть завод обязан был быть
    # git-репозиторием. Теперь обновление приходит теми же запросами к GitHub,
    # что и всё остальное, а адрес движка просто лежит в файле.
    if движок:
        print("  4. Записываю, откуда приходят обновления движка…")
        (data / "engine.source.json").write_text(json.dumps(
            {"engine_repo": движок, "branch": UPSTREAM_BRANCH},
            ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    else:
        print("  4. ⚠️ Репозиторий движка не задан — обновления приходить не будут.")
        print("     Задайте: python zavod_multi.py setup --engine <владелец>/<репо>")

    # 5. Первая копия завода в его собственный репозиторий.
    print("  5. Сохраняю первое состояние…")
    if шаблон and not локальный and not args.dry_run:
        токен = gb.get_token(HERE)
        gb.save_config({"owner": cfg.get("github_account", ""), "repo": репо,
                        "branch": UPSTREAM_BRANCH}, папка)
        try:
            итог = gb.push(токен, папка, quiet=True,
                           message=f"завод клиента {имя}: заведён {сегодня}")
            print(f"     ✔ Сохранено файлов: {итог['total']}")
        except gb.GitHubError as exc:
            # Завод УЖЕ создан и работает; не сохранилась только копия. Это
            # разные беды, и путать их нельзя.
            print(f"     ⚠️ Копия не сохранилась: {exc}")
            print("        Завод при этом рабочий. Повторить: «сделай копию завода».")

    print()
    print("=" * 58)
    print(f"  ЗАВОД ГОТОВ: {имя}")
    print("=" * 58)
    print(f"  Папка:       {папка}")
    if cfg.get("github_account") and шаблон and not локальный:
        print(f"  Репозиторий: https://github.com/{cfg['github_account']}/{репо}")
    print()
    print("  Что дальше:")
    print("   1. Открой эту папку в новом окне Claude Code — увидишь баннер с именем.")
    print("   2. Скажи «проведи распаковку» и дай расшифровку созвона с клиентом.")
    print("      Книга смыслов — фундамент; без неё завод пишет ни о чём.")
    print("   3. Скажи, где папка Google Диска — завод начнёт забирать материалы.")
    print("   4. В конце работы — /handoff, чтобы состояние не потерялось.")
    if args.dry_run:
        print()
        print("  ⚠️ Это был СУХОЙ РЕЖИМ: команды показаны, но не выполнены.")
    return 0


# ── update ───────────────────────────────────────────────────────────────────


def engine_source(папка: Path) -> str:
    """Откуда этому заводу приходят обновления движка. Пусто — неоткуда."""
    try:
        d = json.loads((папка / "data" / "engine.source.json").read_text(encoding="utf-8"))
        return str(d.get("engine_repo") or "")
    except (OSError, ValueError):
        return ""


def one_update(папка: Path, dry: bool = False) -> tuple:
    """Обновить движок в одном заводе. Возвращает (получилось, что сказать).

    Забираем ТОЛЬКО поддерево `engine/` — и это не договорённость, а аргумент
    `prefix`: файл вне `engine/` физически не попадает в выкладку, поэтому
    смыслы и материалы клиента задеть нечем.
    """
    if not (папка / "engine").is_dir():
        return False, "нет папки engine/ — это не завод"
    источник = engine_source(папка)
    if not источник or "/" not in источник:
        return False, "не записано, откуда брать обновления"
    if dry:
        return True, f"[сухой режим] взял бы engine/ из {источник}"
    токен = gb.get_token(HERE)
    if not токен:
        return False, "нет входа в GitHub — настройте пульт"
    owner, repo = источник.split("/", 1)
    try:
        сколько = gb.materialize(токен, owner, repo, папка, prefix="engine/")
    except gb.GitHubError as exc:
        return False, f"обновление не встало: {exc}"
    return True, ("движок уже свежий" if not сколько
                  else f"движок обновлён, файлов заменено: {сколько}")


def cmd_update(args) -> int:
    """Обновление движка. Трогает ТОЛЬКО engine/ — это и есть весь замысел.

    Берём не `git pull`, а `git checkout upstream/main -- engine`: pull попытался
    бы слить ВСЁ дерево, включая `data/`, и на первом же расхождении устроил бы
    клиенту конфликт слияния в его собственных смыслах. Точечная выемка одного
    пути такого не умеет по устройству команды.
    """
    if args.all:
        корень = factories_dir()
        заводы = sorted(p for p in корень.iterdir()
                        if p.is_dir() and (p / "engine").is_dir()) \
            if корень.is_dir() else []
        if not заводы:
            print(f"В {корень} заводов не нашлось.")
            return 1
        print(f"Обновляю движок во всех заводах: {len(заводы)}")
        плохо = 0
        for п in заводы:
            получилось, что = one_update(п, dry=args.dry_run)
            print(f"  {'✔' if получилось else '🔴'} {п.name}: {что}")
            плохо += 0 if получилось else 1
        print()
        print(f"Обновлено: {len(заводы) - плохо} из {len(заводы)}")
        return 0 if плохо == 0 else 1

    # resolve() обязателен: `--dir .` даёт Path(".").name == "" и отчёт
    # выходил безымянным — «✔ : движок обновлён».
    папка = (Path(args.dir).expanduser() if args.dir else Path.cwd()).resolve()
    получилось, что = one_update(папка, dry=args.dry_run)
    print(f"{'✔' if получилось else '🔴'} {папка.name}: {что}")
    if получилось and not args.dry_run:
        print("   Данные клиента не тронуты — обновление трогает только engine/.")
        print("   Убедиться: git status")
    return 0 if получилось else 1


# ── list ─────────────────────────────────────────────────────────────────────


def cmd_list(args) -> int:
    корень = factories_dir()
    if not корень.is_dir():
        print(f"Папки заводов ещё нет: {корень}")
        print("Заведи первую: python zavod_multi.py setup")
        return 1
    заводы = sorted(p for p in корень.iterdir() if p.is_dir())
    if not заводы:
        print(f"В {корень} пока пусто.")
        return 0
    print(f"Заводы в {корень}:")
    for п in заводы:
        cfg_path = п / "data" / "client.config.json"
        имя = "?"
        try:
            имя = json.loads(cfg_path.read_text(encoding="utf-8")).get("name", "?")
        except (OSError, ValueError):
            pass
        завод = "завод" if (п / "engine").is_dir() else "не завод"
        print(f"  {п.name:<24} {имя:<28} {завод}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Пульт маркетолога: заводы клиентов")
    sub = parser.add_subparsers(dest="команда")

    p = sub.add_parser("setup", help="разовая настройка пульта")
    p.add_argument("--dir", help="папка заводов")
    p.add_argument("--template", help="репозиторий-шаблон, <владелец>/<репо>")
    p.add_argument("--engine", help="репозиторий движка для обновлений")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("new", help="завести завод новому клиенту")
    p.add_argument("name", nargs="?", help="имя клиента")
    p.add_argument("--slug", help="имя папки латиницей (обычно не нужно)")
    p.add_argument("--template", help="репозиторий-шаблон")
    p.add_argument("--engine", help="репозиторий движка")
    p.add_argument("--dry-run", action="store_true",
                   help="показать шаги, ничего не делать")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("update", help="обновить движок")
    p.add_argument("--all", action="store_true", help="во всех заводах")
    p.add_argument("--dir", help="папка завода (по умолчанию текущая)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("list", help="какие заводы есть")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("slug", help="проверить транслитерацию имени")
    p.add_argument("name")
    p.set_defaults(func=lambda a: (print(slugify(a.name)), 0)[1])

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
