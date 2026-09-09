# -*- coding: utf-8 -*-
"""Генератор структуры клиентского Google Диска (контракт intake).

Единственный источник правды — `drive_structure.json` рядом с этим файлом
(человеческая версия: docs/СТРУКТУРА_ДИСКА.md). Здесь только код, который
раскладывает контракт на реальном диске.

Что делает:
    * создаёт дерево папок «КонтентЗавод/...» внутри папки Google Диска;
    * кладёт в каждую папку файл-подсказку «ЧТО СЮДА КЛАСТЬ.txt» простым языком;
    * кладёт в корень «КАК ЭТО РАБОТАЕТ.txt» — общая карта для клиента;
    * пишет карточку клиента в служебную папку (кто владелец этого диска).

Идемпотентность (важно, запускается при каждой установке/донастройке):
    * папки создаются с exist_ok — существующие не трогаются;
    * файлы-подсказки пишутся ТОЛЬКО если их ещё нет; уже лежащий файл
      никогда не перезаписывается, даже если текст в контракте поменялся;
    * старая структура (Загрузить/Готово/...) не удаляется и не переименовывается —
      просто рядом появляется новая, а доктор подскажет перенести файлы руками.

Никаких абсолютных путей и секретов в коде. Корень диска приходит снаружи
(из config/factory.config.json, ключ gdrive_root) или аргументом --root.

Запуск вручную:
    python -m intake.drive_layout --root "<папка Google Диска>" [--client "Имя"]
    python -m intake.drive_layout --root "..." --dry-run   # только показать план
    python -m intake.drive_layout --print-tree             # дерево из контракта
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

CONTRACT_FILE = "drive_structure.json"


# --------------------------------------------------------------------------
# контракт
# --------------------------------------------------------------------------
def load_contract(path=None):
    """Читает контракт структуры диска. path — для тестов/переопределения."""
    p = Path(path) if path else Path(__file__).resolve().parent / CONTRACT_FILE
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def factory_root(gdrive_root, contract=None):
    """Путь к папке «КонтентЗавод» внутри папки Google Диска.

    Если клиент указал сразу саму «КонтентЗавод» — второй раз её не создаём.
    """
    contract = contract or load_contract()
    base = Path(gdrive_root).expanduser()
    root_name = contract["root"]
    return base if base.name == root_name else base / root_name


def folder_paths(gdrive_root, contract=None):
    """{id папки: абсолютный путь} — для остальных модулей завода."""
    contract = contract or load_contract()
    root = factory_root(gdrive_root, contract)
    return {f["id"]: root.joinpath(*f["path"].split("/")) for f in contract["folders"]}


def pipeline_map(gdrive_root, contract=None):
    """{имя папки конвейера: путь на диске} — talking/photos/video/music/stock/out_talk.

    Именно эта карта связывает «человеческие» русские папки на диске клиента
    с рабочими каталогами движка. Симлинки/копирование делает вызывающий код.
    """
    contract = contract or load_contract()
    root = factory_root(gdrive_root, contract)
    out = {}
    for f in contract["folders"]:
        if f.get("pipeline_dir"):
            out[f["pipeline_dir"]] = root.joinpath(*f["path"].split("/"))
    return out


def render_tree(contract=None):
    """Дерево контракта текстом — показать человеку в мастере или в доке."""
    contract = contract or load_contract()
    lines = ["%s/" % contract["root"]]
    for f in contract["folders"]:
        parts = f["path"].split("/")
        depth = len(parts) - 1
        mark = "" if f.get("client_visible", True) else "   (служебное)"
        lines.append("%s%s/%s" % ("    " * (depth + 1), parts[-1], mark))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# создание структуры
# --------------------------------------------------------------------------
def _write_if_absent(path, text, report, dry_run):
    """Пишет файл, только если его ещё нет. Возвращает True, если создали."""
    if path.exists():
        report["kept_files"].append(str(path))
        return False
    report["created_files"].append(str(path))
    if not dry_run:
        # utf-8 с BOM и \r\n — клиент чаще всего на Windows и открывает это
        # Блокнотом; без этого он видит кракозябры и текст одной строкой.
        # (Path.write_text(newline=) появился только в 3.10 — пишем через open.)
        with path.open("w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text)
    return True


def ensure_structure(gdrive_root, client_name=None, contract=None, dry_run=False):
    """Создаёт (или дополняет) структуру на диске клиента.

    Возвращает отчёт-словарь:
        root            — путь к «КонтентЗавод»
        created_dirs    — какие папки завели сейчас
        kept_dirs       — какие уже были
        created_files   — какие подсказки положили
        kept_files      — какие подсказки уже лежали (не тронуты)
        legacy          — найденные папки старой структуры
        errors          — что не удалось (нет прав, диск не смонтирован и т.п.)
    """
    contract = contract or load_contract()
    root = factory_root(gdrive_root, contract)
    report = {
        "root": str(root),
        "contract_version": contract.get("contract_version"),
        "created_dirs": [],
        "kept_dirs": [],
        "created_files": [],
        "kept_files": [],
        "legacy": [],
        "errors": [],
        "dry_run": bool(dry_run),
    }

    base = Path(gdrive_root).expanduser()
    if not base.exists():
        report["errors"].append(
            "Папки Google Диска нет на этом компьютере: %s. "
            "Проверьте, что Google Диск установлен и синхронизация включена." % base
        )
        return report

    # 1) корень «КонтентЗавод»
    try:
        if root.exists():
            report["kept_dirs"].append(str(root))
        else:
            report["created_dirs"].append(str(root))
            if not dry_run:
                root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report["errors"].append(
            "Не удалось создать папку «%s»: %s. Похоже, нет прав на запись." % (root, exc)
        )
        return report

    # 2) корневая памятка
    root_readme = root / contract["root_readme_filename"]
    if dry_run and not root.exists():
        report["created_files"].append(str(root_readme))
    else:
        try:
            _write_if_absent(root_readme, "\n".join(contract["root_readme"]) + "\n",
                             report, dry_run)
        except OSError as exc:
            report["errors"].append("Не удалось записать «%s»: %s" % (root_readme.name, exc))

    # 3) все папки контракта + подсказки внутри
    readme_name = contract["readme_filename"]
    for f in contract["folders"]:
        target = root.joinpath(*f["path"].split("/"))
        try:
            if target.exists():
                report["kept_dirs"].append(str(target))
            else:
                report["created_dirs"].append(str(target))
                if not dry_run:
                    target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            report["errors"].append("Не удалось создать папку «%s»: %s" % (f["path"], exc))
            continue

        body = ["=== %s ===" % f["title"], ""]
        body.extend(f["readme"])
        accepts = f.get("accepts")
        if accepts:
            body.append("")
            body.append("Форматы файлов: " + ", ".join(accepts))
        body.append("")
        body.append("(Этот файл создал Контент-завод, чтобы вы не гадали. Удалять можно.)")
        target_readme = target / readme_name
        if dry_run and not target.exists():
            report["created_files"].append(str(target_readme))
            continue
        try:
            _write_if_absent(target_readme, "\n".join(body) + "\n", report, dry_run)
        except OSError as exc:
            report["errors"].append("Не удалось записать подсказку в «%s»: %s"
                                    % (f["path"], exc))

    # 4) карточка клиента (кто владелец этого диска) — у каждого клиента свой диск
    marker = root.joinpath(*contract["client_marker"].split("/"))
    if not marker.exists():
        card = {
            "client": client_name or "",
            "contract_version": contract.get("contract_version"),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "_note": "Карточка этого диска. По ней завод понимает, чей это диск и "
                     "какой версии структура. Не удалять.",
        }
        report["created_files"].append(str(marker))
        if not dry_run:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps(card, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                report["errors"].append("Не удалось записать карточку клиента: %s" % exc)
    else:
        report["kept_files"].append(str(marker))

    # 5) старая структура — только сообщаем, ничего не двигаем
    for old_name in contract.get("legacy_aliases", {}):
        if old_name.startswith("_"):
            continue
        old = root / old_name
        new_id = contract["legacy_aliases"][old_name]
        if not old.exists():
            continue
        # «НЕ_ТРОГАТЬ» есть и в новой структуре — это не legacy
        if any(f["path"] == old_name for f in contract["folders"]):
            continue
        try:
            has_files = any(old.iterdir())
        except OSError:
            has_files = False
        report["legacy"].append({"folder": old_name, "new_id": new_id,
                                 "has_files": has_files})

    return report


def read_client_card(gdrive_root, contract=None):
    """Карточка клиента с диска или None. Нужна, когда дисков несколько."""
    contract = contract or load_contract()
    marker = factory_root(gdrive_root, contract).joinpath(
        *contract["client_marker"].split("/"))
    try:
        with marker.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_report(report):
    if report["dry_run"]:
        print("Пробный запуск — на диске ничего не менялось.")
    print("Папка завода: %s" % report["root"])
    print("Папок создано: %d, уже было: %d" % (len(report["created_dirs"]),
                                               len(report["kept_dirs"])))
    print("Подсказок создано: %d, оставлено как есть: %d" % (len(report["created_files"]),
                                                             len(report["kept_files"])))
    for item in report["created_dirs"]:
        print("  + %s" % item)
    for legacy in report["legacy"]:
        note = " (в ней есть файлы — перенесите их в новые папки)" if legacy["has_files"] else ""
        print("  ! осталась папка старой структуры «%s»%s" % (legacy["folder"], note))
    for err in report["errors"]:
        print("  ОШИБКА: %s" % err)


def connector_plan(contract=None):
    """План для создания структуры ЧЕРЕЗ КОННЕКТОР Google Drive (не локально).

    Клод читает этот JSON и создаёт папки/README прямо на диске клиента через
    инструменты коннектора. Тексты берутся из того же контракта, что и локальный
    путь — структура получается идентичная. Ничего не создаёт сам."""
    contract = contract or load_contract()
    return {
        "root": contract["root"],
        "root_readme_filename": contract.get("root_readme_filename", "КАК ЭТО РАБОТАЕТ.txt"),
        "root_readme": "\n".join(contract.get("root_readme", [])),
        "readme_filename": contract.get("readme_filename", "ЧТО СЮДА КЛАСТЬ.txt"),
        "folders": [
            {"path": f["path"], "readme": "\n".join(f.get("readme", []))}
            for f in contract["folders"]
        ],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Создаёт структуру папок Контент-завода на диске клиента.")
    ap.add_argument("--root", help="папка Google Диска на этом компьютере")
    ap.add_argument("--client", default=None, help="имя клиента для карточки диска")
    ap.add_argument("--dry-run", action="store_true", help="показать план, ничего не создавать")
    ap.add_argument("--print-tree", action="store_true", help="показать дерево из контракта")
    ap.add_argument("--connector-plan", action="store_true",
                    help="JSON-план для создания структуры через коннектор Google Drive")
    ap.add_argument("--json", action="store_true", help="машинный вывод отчёта")
    args = ap.parse_args(argv)

    if args.print_tree:
        print(render_tree())
        return 0

    if args.connector_plan:
        print(json.dumps(connector_plan(), ensure_ascii=False, indent=2))
        return 0

    if not args.root:
        ap.error("нужен --root (папка Google Диска) или --print-tree")

    report = ensure_structure(args.root, client_name=args.client, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
