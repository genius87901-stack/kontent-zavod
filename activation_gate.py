# -*- coding: utf-8 -*-
"""
activation_gate — тонкая обёртка гейта активации для точек выхода завода (BOX-29).

ЗАЧЕМ. Публикация и рендер не должны идти на неактивированной клиентской копии.
Этот модуль изолирует импорт `installer/activation.py` (он лежит в подкаталоге) и
даёт одну функцию `allow(action)`. Точки выхода (autopost, story_publisher,
render_reels) зовут её перед реальной работой.

БЕЗОПАСНОСТЬ ПО УМОЛЧАНИЮ. Если сам гейт по какой-то причине не грузится (сломанная
сборка), НЕ роняем завод из-за нашей же ошибки — пускаем и пишем предупреждение.
В штатной клиентской сборке activation.py присутствует (preflight это гарантирует),
поэтому реальная защита сохраняется; послабление касается только аварийного случая.
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.abspath(__file__))
_INSTALLER = os.path.join(_REPO, "installer")


def _load():
    if _INSTALLER not in sys.path:
        sys.path.insert(0, _INSTALLER)
    import activation as _act  # noqa: WPS433
    return _act


def allow(action: str = "эту операцию") -> bool:
    """True — можно выполнять `action`. False — гейт заблокировал (жёсткий режим).

    Печатает человекочитаемую причину при блокировке. `action` — что именно
    блокируется («публикацию», «рендер») для понятного сообщения.
    """
    try:
        act = _load()
        root = act.find_root()
    except Exception as e:  # noqa: BLE001 — гейт не должен рушить завод собственной ошибкой
        print(f"[активация] гейт недоступен ({e}) — операция разрешена.", file=sys.stderr)
        return True
    ok, status, msg = act.require_activation(root)
    if ok:
        return True
    if act.is_enforced(root):
        print(f"\n[активация] {action.upper()} ОСТАНОВЛЕНА: {msg}\n"
              f"Активируйте лицензию:  python installer/activation.py --check license.key",
              file=sys.stderr)
        return False
    print(f"[активация] предупреждение ({status}): {msg} "
          f"[мягкий режим — {action} не блокируется]", file=sys.stderr)
    return True


def check():
    """(ok, status, message) — для диагностики/тестов."""
    act = _load()
    return act.require_activation(act.find_root())
