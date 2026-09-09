# -*- coding: utf-8 -*-
"""doctor модуля github_sync (резервная копия завода на GitHub).

Проверяет, есть ли у клиента живая копия завода, и печатает ПО-РУССКИ, что
сделать. Без стектрейсов, без сети в обычном режиме.

Что проверяет:
    1. лежит ли на месте сам инструмент (github_backup.py в корне установки);
    2. выполнен ли вход в GitHub;
    3. заведён ли приватный репозиторий;
    4. когда делалась последняя копия и не устарела ли она.

Коды выхода (степень беды, CONTRACT.md §2 — НЕ «да/нет»):
    0  — копия есть и свежая.
    2  — урезанный режим: копии нет ИЛИ она застарела. Завод при этом работает
         полностью, поэтому это строка суточной сводки, а не красная тревога.
    1  — инструмента нет на месте: коробка приехала неполной, чинится патчем.

⚠️ ПОЧЕМУ ДОКТОР ГОВОРИТ ГРОМКО ПРО «НЕ НАСТРОЕНО» (урок BOX-73). Логин в
GitHub — это личный аккаунт клиента, и выполнить его за человека нельзя ничем.
Значит патч, привозящий этот модуль, ТРЕБУЕТ ручного шага — ровно тот случай,
на котором 1.3.44 отчитался успехом у трёх клиентов и выбил у них приём
материалов. Поэтому «не настроено» здесь не молчание и не код 0: это код 2 с
одной понятной командой. Человек, у которого копии нет, обязан об этом узнать
из обычной сводки, а не в день, когда у него умер диск.

Устаревание считаем по календарю, а не по факту изменений: копия, которой
больше STALE_DAYS дней, скорее всего не делается вовсе (кончился доступ,
сменился пароль, отозван токен). Молчать об этом нельзя — неработающая
резервная копия опаснее отсутствующей, потому что на неё рассчитывают.

Запуск: python -m github_sync.doctor
Починить: python github_backup.py --login, затем --setup

Никаких абсолютных путей, никакого input(). Только stdlib.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Коды возврата по CONTRACT.md §2 — именами, чтобы на каждой точке выхода было
# видно решение, а не голая цифра.
EXIT_OK = 0
EXIT_LIMITED = 2
EXIT_BROKEN = 1

#: Дней, после которых копия считается застарелой. Неделя — компромисс: реже
#: раза в неделю живой завод не работает вовсе, а дёргать человека на второй
#: день простоя незачем.
STALE_DAYS = 7

CONFIG_REL = Path("config") / "github.json"
TOOL_REL = Path("github_backup.py")
ENV_REL = Path(".env")
TOKEN_KEY = "GITHUB_TOKEN"


def _root() -> Path:
    """Корень установки: вверх по дереву от модуля до маркера."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".factory_root").exists() or (parent / "CONTRACT.md").exists():
            return parent
        if (parent / "modules").is_dir() and (parent / "config").is_dir():
            return parent
    return here.parents[2]


def _token_present(root: Path) -> bool:
    """Есть ли доступ в .env. Значение НЕ читаем и не печатаем — только факт."""
    try:
        text = (root / ENV_REL).read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        if line.strip().startswith(TOKEN_KEY + "="):
            return bool(line.split("=", 1)[1].strip())
    return False


def _config(root: Path) -> dict:
    try:
        data = json.loads((root / CONFIG_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    root = _root()
    print("Резервная копия завода на GitHub")

    if not (root / TOOL_REL).exists():
        print("  🔴 Инструмента github_backup.py нет в папке завода.")
        print("     Коробка приехала неполной — это чинится обновлением:")
        print("     python updater.py --apply")
        return EXIT_BROKEN

    есть_вход = _token_present(root)
    cfg = _config(root)
    заведён = bool(cfg.get("owner") and cfg.get("repo"))

    if not есть_вход or not заведён:
        print("  ⚠️ Копии завода пока нет — он существует ровно в одном месте,")
        print("     на этом компьютере. Сломается диск — пропадут и код, и смыслы.")
        print("     Включается один раз, вход в ВАШ GitHub:")
        print("       python github_backup.py --login")
        print("       python github_backup.py --setup")
        print("     Ключи, фото и видео в копию не попадают никогда.")
        return EXIT_LIMITED

    адрес = f"https://github.com/{cfg['owner']}/{cfg['repo']}"
    last = cfg.get("last_push") or {}
    когда = last.get("at")
    if not когда:
        print(f"  ⚠️ Репозиторий заведён ({адрес}), но копия ещё ни разу не уехала.")
        print("     Одна команда: python github_backup.py --push")
        return EXIT_LIMITED

    try:
        отметка = time.mktime(time.strptime(когда, "%Y-%m-%dT%H:%M:%S"))
        дней = int((time.time() - отметка) // 86400)
    except (ValueError, OverflowError):
        дней = None

    print(f"  репозиторий:     {адрес} (приватный)")
    print(f"  последняя копия: {когда} · файлов {last.get('files', '?')}")

    if дней is not None and дней > STALE_DAYS:
        print(f"  ⚠️ Копия не обновлялась {дней} дней. Обычно она уезжает сама при")
        print("     запуске помощника — значит что-то мешает: чаще всего кончился")
        print("     доступ к GitHub. Проверить одной командой:")
        print("       python github_backup.py --push")
        print("     Копия, на которую рассчитывают и которая не делается, хуже,")
        print("     чем её отсутствие — поэтому говорю вслух.")
        return EXIT_LIMITED

    print("  ✔ Копия свежая. Код, смыслы и настройки продублированы.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
