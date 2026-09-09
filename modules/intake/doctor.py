# -*- coding: utf-8 -*-
"""doctor модуля intake (приём сырья).

Проверяет готовность приёма материалов клиента и печатает ПО-РУССКИ
человеческим языком, что не так и что сделать. Без стектрейсов.

Что проверяет:
    1. указана ли папка Google Диска (конфиг, ключ gdrive_root) и есть ли она;
    2. на месте ли структура «КонтентЗавод» из контракта (drive_structure.json);
    3. можно ли в неё писать (реальная проба записи, а не только os.access);
    4. не осталась ли старая структура (Загрузить/Готово/Опубликовано);
    5. есть ли новое сырьё и сколько черновиков ждёт решения человека.

Коды выхода (степень беды, CONTRACT.md §2 — НЕ «да/нет»):
    0  — приём материалов работает (мелкие замечания печатаются, но не валят).
    2  — урезанный режим: завод работает, автоматического приёма с Диска нет.
         Клиенту НЕ надо бросать всё и чинить прямо сейчас — это обычная
         строка суточной сводки, а не красная тревога.
    1  — не готов: главного пути нет ИЛИ сама проверка не смогла отработать.
         Уходит владельцу красным «🔴 СРОЧНО», поэтому ставится только там,
         где человеку и правда надо вмешаться немедленно.

Запуск (его дёргает мастер установки): python -m intake.doctor
Починить структуру: python -m intake.drive_layout --root "<папка Google Диска>"

НЕДОСТАЮЩИЕ ПАПКИ ДОКТОР ЗАВОДИТ САМ (BOX-72/73, живой случай 01.09.2026).
Патч 1.3.44 привёз контракт версии 3 с двумя новыми папками («Длинные видео»,
«Нарезка из эфиров») — у старых клиентов их на Диске не было, и приём падал в
урезанный режим. Лечилось одной командой, но узнать о ней было неоткуда:
обновление выглядело успешным, а завод переставал принимать материалы. У ТРЁХ
живых клиентов приём был ЕДИНСТВЕННЫМ работавшим модулем — стало ноль из восьми.

Поэтому доктор, увидев нехватку папок, молча не уходит: он зовёт
drive_layout.ensure_structure() и заводит недостающее сам. Это безопасно —
генератор идемпотентен, только создаёт, ничего не удаляет и не переименовывает.
Что именно завёл, доктор говорит вслух. Не получилось (нет прав, диск отключён)
— печатает прежнее объяснение с командой.

Нужна чистая диагностика без правки диска — флаг --no-repair.

Никаких абсолютных путей в коде, никакого input(). Путь к Google Диску
берётся только из конфига (ключ gdrive_root), папки не угадываются.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

CONFIG_REL = Path("config") / "factory.config.json"
# Сколько дней считаем материал «свежим» (просто для дружелюбной фразы).
FRESH_DAYS = 14

# Коды возврата по CONTRACT.md §2. Именами, чтобы на каждой точке выхода было
# видно решение, а не голая цифра.
EXIT_OK = 0          # всё работает
EXIT_LIMITED = 2     # урезанный режим — обычная строка сводки
EXIT_NOT_READY = 1   # красное «🔴 СРОЧНО» владельцу


def _import_layout():
    """drive_layout лежит рядом; поддерживаем и -m intake.doctor, и прямой запуск."""
    try:
        from intake import drive_layout
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from intake import drive_layout
    return drive_layout


def find_root(start):
    """Корень установки. Идём вверх от ЭТОГО ФАЙЛА (modules/intake/doctor.py).

    Маркеров два, и порядок важен. `.factory_root` кладёт установщик, он есть в
    КАЖДОЙ клиентской коробке. `CONTRACT.md` в коробку НЕ едет (build_box его не
    возит — это документ разработки), поэтому у клиента поиск по нему не находил
    ничего и срабатывал запасной вариант.
    А запасной был сдвинут на уровень: сюда приходит путь ФАЙЛА, значит
    parents[0] = modules/intake, parents[1] = modules, и корнем объявлялась
    папка `modules`. Конфига там нет — и `intake` у всех клиентов отвечал
    «не указана папка Google Диска» даже тогда, когда она указана. Правильный
    уровень — parents[2], как в config.py остальных четырёх модулей.
    """
    # ⚠️ FACTORY_ROOT — ПЕРВЫМ (02.09.2026, разделение engine/ и data/).
    # Мультиклиентный завод кладёт код в `engine/`, а данные клиента в `data/`;
    # идя вверх от `engine/modules/...`, до `data/` не дойти никогда — деревья
    # соседние. Поэтому явно заданный корень сильнее поиска по маркерам.
    # Это же чинит известное расхождение: `factory_config` и
    # `publishing/config_reader` FACTORY_ROOT уважают, а этот поиск — нет, и
    # подставленный корень молча игнорировался (тот же класс, что записан в
    # уроках про `analytics/upload_post.py`).
    _задан = os.environ.get("FACTORY_ROOT")
    if _задан and Path(_задан).is_dir():
        return Path(_задан)
    for parent in [start, *start.parents]:
        if (parent / ".factory_root").exists() or (parent / "CONTRACT.md").exists():
            return parent
    # запасной вариант: .../modules/intake/doctor.py -> parents[2]
    return start.parents[2] if len(start.parents) >= 3 else start


def read_config(root):
    """Читает config/factory.config.json. Возвращает (dict, ошибка_текстом|None)."""
    cfg_path = root / CONFIG_REL
    if not cfg_path.exists():
        return {}, None  # конфига ещё нет — это не поломка, разберёмся выше
    try:
        with cfg_path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, "файл настроек повреждён (ожидался список настроек)."
        return data, None
    except (OSError, ValueError) as exc:
        return {}, f"не удалось прочитать файл настроек: {exc}"


def resolve_gdrive(root, value):
    """Путь из конфига: если он относительный — считаем от корня установки."""
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def _probe_path(folder):
    """Путь пробного файла — СВОЙ у каждого процесса и каждой попытки.

    Раньше имя было фиксированным («.завод_проба_записи.tmp»), и две проверки,
    идущие одновременно, дрались за один файл: вторая удаляла файл первой, у
    первой падал unlink() — и обе рапортовали «записать не получается».
    17.08.2026 в 19:54:40Z одна клиентская копия за секунду прислала три отчёта:
    два intake=no_write и один intake=ok, при этом у клиента висело красное
    «🔴 СРОЧНО», хотя с правами всё было в порядке.
    PID разводит разные процессы, uuid4 — потоки и повторные вызовы внутри
    одного процесса.
    """
    return folder / f".завод_проба_записи.{os.getpid()}.{uuid.uuid4().hex}.tmp"


def can_write(folder):
    """Реальная проба записи: создаём и сразу удаляем крошечный файл.

    Возвращает (можно_писать, техническая_причина|None).

    Два разных случая при уборке различаем явно:
      * файла уже нет (FileNotFoundError) — запись-то прошла, вердикт «можно».
        Так бывает, если пробу подмёл чистильщик диска или сам Google Диск;
      * любая другая ошибка (нет прав, том «только для чтения») — это ровно
        то, что функция обязана ловить, её НЕ проглатываем.
    """
    probe = _probe_path(folder)
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        return False, str(exc)

    try:
        probe.unlink()
    except FileNotFoundError:
        pass  # кто-то убрал пробу раньше нас — записать всё равно получилось
    except OSError as exc:
        # Записать смогли, а удалить — нет. Это настоящая проблема с правами:
        # файл останется мусором на клиентском диске, молчать нельзя.
        return False, str(exc)
    return True, None


def count_files(folder, fresh_days=FRESH_DAYS):
    """(всего файлов, из них свежих) внутри папки, рекурсивно. Служебное не считаем."""
    total = fresh = 0
    cutoff = time.time() - fresh_days * 86400
    skip_names = ("ЧТО СЮДА КЛАСТЬ.txt", "КАК ЭТО РАБОТАЕТ.txt", "desktop.ini")
    try:
        for p in folder.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith(".") or p.name.endswith(".tmp"):
                continue
            if p.name in skip_names:
                continue
            total += 1
            try:
                if p.stat().st_mtime >= cutoff:
                    fresh += 1
            except OSError:
                pass
    except OSError:
        pass
    return total, fresh


def _plural(n, one, few, many):
    """Русские окончания: 1 файл / 2 файла / 5 файлов."""
    tail = abs(n) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    no_repair = "--no-repair" in argv
    layout = _import_layout()
    contract = layout.load_contract()

    here = Path(__file__).resolve()
    root = find_root(here)
    cfg, cfg_err = read_config(root)

    if cfg_err:
        print("Приём материалов пока не настроен.")
        print(f"Причина: {cfg_err}")
        print("Что делать: запусти установку заново или попроси помочь того, "
              "кто ставил завод — сам файл настроек трогать не нужно.")
        # 1: сама проверка не смогла отработать — читать нечего, вердикта нет
        # (CONTRACT.md §2, дословный случай «doctor не смог проверить»).
        return EXIT_NOT_READY

    gdrive = (cfg.get("gdrive_root") or "").strip()
    if not gdrive:
        print("Не указана папка Google Диска — завод не сможет сам забирать "
              "твои фото и видео.")
        print("Что делать: укажи, где на этом компьютере лежит папка Google "
              "Диска (та, куда телефон синхронизирует файлы). Внутри неё "
              "завод сам заведёт папку «КонтентЗавод», а в ней «1 Сырьё» — "
              "туда ты и будешь кидать кадры с телефона.")
        print("Пока папка не указана, всё остальное работает — не хватает "
              "только автоматического приёма материалов.")
        # 2: незаполненный ключ конфига. Клиент просто ещё не настроил приём,
        # завод жив. Ровно тот случай, из-за которого летели ложные красные
        # тревоги: в выводе «всё остальное работает», а код был 1.
        return EXIT_LIMITED

    base = resolve_gdrive(root, gdrive)

    if not base.exists():
        print("Папка Google Диска указана, но на этом компьютере её нет:")
        print(f"    {base}")
        print("Что делать: проверь, что Google Диск установлен и синхронизация "
              "включена, либо укажи правильную папку. Возможно, в пути опечатка.")
        # 2: то же «не настроено», только с опечаткой в пути. Приёма нет,
        # остальной завод работает — бросать всё и чинить сейчас не нужно.
        return EXIT_LIMITED

    factory = layout.factory_root(base, contract)

    if not factory.exists():
        print("Папка Google Диска найдена, но внутри ещё нет папки "
              "«КонтентЗавод» — значит, приёмник для твоих материалов не заведён.")
        print("Что делать: попроси донастроить завод — папку «КонтентЗавод» "
              "и её содержимое создаёт установка, вручную заводить не нужно.")
        print(f'    Команда для установщика: python -m intake.drive_layout --root "{base}"')
        # 2: донастройка, а не поломка — чинится одной командой установщика,
        # ничего при этом не потеряно.
        return EXIT_LIMITED

    # --- структура из контракта ---
    paths = layout.folder_paths(base, contract)
    by_id = {f["id"]: f for f in contract["folders"]}
    missing = [by_id[fid]["path"] for fid, p in paths.items() if not p.exists()]

    if missing and not no_repair:
        # BOX-72: не отправляем человека выполнять команду — заводим сами.
        # ensure_structure идемпотентен: только создаёт, ничего не удаляет.
        errors = []
        try:
            report = layout.ensure_structure(base)
            errors = report.get("errors") or []
        except OSError as e:
            errors = [str(e)]
        paths = layout.folder_paths(base, contract)
        missing = [by_id[fid]["path"] for fid, p in paths.items() if not p.exists()]
        if not missing:
            print("В «КонтентЗавод» не хватало папок — завёл их сам:")
            for m in report.get("created_dirs") or []:
                print(f"    • {m}")
            print("Файлы, которые уже лежали на диске, не тронуты.")
        elif errors:
            print("Не хватает папок в «КонтентЗавод», и завести их не вышло:")
            for e in errors[:3]:
                print(f"    {e}")

    if missing:
        print("Папка «КонтентЗавод» есть, но внутри не хватает папок:")
        for m in missing:
            print(f"    • {m}")
        print("Что делать: это чинится одной командой, ничего не потеряется —")
        print(f'    python -m intake.drive_layout --root "{base}"')
        print("Файлы, которые уже лежат на диске, она не трогает.")
        # 2: часть возможностей недоступна (не все папки конвейера заведены),
        # чинится той же одной командой. Главный путь при этом не разрушен.
        return EXIT_LIMITED

    # --- права на запись ---
    ok, err = can_write(factory)
    if not ok:
        print("Папка «КонтентЗавод» есть, но записать в неё не получается.")
        print(f"Техническая причина: {err}")
        print("Что делать: чаще всего это значит, что Google Диск открыл папку "
              "«только для чтения» или закончилось место. Проверь свободное место "
              "и что синхронизация не на паузе, потом попробуй ещё раз.")
        # 1: единственный случай здесь, где красное «СРОЧНО» заслужено. Всё
        # настроено и раньше работало, а теперь сломалось: кончилось место или
        # Диск перевёл папку в «только для чтения». Обмена через Диск нет ни в
        # одну сторону — присланное с телефона не доезжает, готовое не выложить,
        # и починки «одной командой» не существует, нужен человек. Это «главного
        # пути нет» по CONTRACT.md §2, а не «часть возможностей недоступна».
        return EXIT_NOT_READY

    # --- всё хорошо; дальше только полезные наблюдения ---
    print("Приём материалов готов.")
    print(f"Папка Google Диска: {base}")
    card = layout.read_client_card(base, contract)
    if card and card.get("client"):
        print(f"Диск оформлен на: {card['client']}")

    raw_total, raw_fresh = count_files(paths["raw"])
    if raw_total == 0:
        print("Сырья пока нет. Кидай фото и видео с телефона в «1 Сырьё» — "
              "внутри есть подпапки, а если не уверен, куда именно, клади прямо в неё.")
    else:
        word = _plural(raw_total, "файл", "файла", "файлов")
        line = f"В «1 Сырьё» лежит {raw_total} {word}"
        if raw_fresh:
            line += f", из них {raw_fresh} за последние {FRESH_DAYS} дней"
        print(line + ".")

    # что где — по подпапкам сырья, чтобы человек видел перекос
    details = []
    for fid in ("raw.talking", "raw.photos", "raw.video", "raw.music", "raw.notes"):
        if fid not in paths:
            continue
        n, _ = count_files(paths[fid])
        if n:
            details.append(f"{by_id[fid]['title'].lower()} — {n}")
    if details:
        print("  Разложено так: " + ", ".join(details) + ".")

    review_total, _ = count_files(paths["review"])
    approved, _ = count_files(paths["review.approved"])
    rework, _ = count_files(paths["review.rework"])
    waiting = max(review_total - approved - rework, 0)
    if waiting:
        word = _plural(waiting, "черновик", "черновика", "черновиков")
        print(f"Тебя ждёт {waiting} {word} в «2 На утверждение» — посмотри и разложи "
              "по папкам «Одобрено» или «Вернуть на правки».")
    if approved:
        word = _plural(approved, "работа", "работы", "работ")
        print(f"Одобрено и ждёт выпуска: {approved} {word}.")

    # --- старая структура: не ошибка, но сказать надо ---
    legacy_found = []
    for old_name, new_id in contract.get("legacy_aliases", {}).items():
        if old_name.startswith("_"):
            continue
        if any(f["path"] == old_name for f in contract["folders"]):
            continue  # «НЕ_ТРОГАТЬ» есть и в новой раскладке — это не старьё
        old = factory / old_name
        if not old.exists():
            continue
        n, _ = count_files(old)
        legacy_found.append((old_name, new_id, n))
    if legacy_found:
        print("")
        print("Замечание (работать не мешает): остались папки от прежней раскладки.")
        for old_name, new_id, n in legacy_found:
            target = by_id[new_id]["path"]
            tail = (f" — в ней {n} шт., перенеси их в «{target}»" if n
                    else " — пустая, можно удалить")
            print(f"    • «{old_name}»{tail}")

    return EXIT_OK


def _run():
    """Обёртка для запуска: клиент не должен видеть стектрейс (CONTRACT.md §2).

    Если проверка упала на чём-то непредвиденном — это «сама проверка не смогла
    отработать», то есть код 1, но человеческим текстом.
    """
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — наружу летит текст, а не трассировка
        print("Не удалось проверить приём материалов — сама проверка сломалась.")
        print(f"Техническая причина: {type(exc).__name__}: {exc}")
        print("Что делать: покажи эту строчку тому, кто ставил завод.")
        return EXIT_NOT_READY


if __name__ == "__main__":
    sys.exit(_run())
