# -*- coding: utf-8 -*-
"""
machine_fingerprint — стабильный отпечаток машины для привязки лицензии (BOX-29, фаза 1).

ЗАЧЕМ. Лицензия привязывается к МАШИНЕ (копии завода), а не к соцсетям. При активации
считаем односторонний хеш стабильных идентификаторов железа/ОС; при каждом старте
сверяем. Копирование папки завода на другую машину даёт другой отпечаток → повторная
активация (в фазе 1 — мягко; жёсткий контроль числа машин придёт с онлайн-реестром в фазе 2).

ПРИВАТНОСТЬ. Наружу и в запись идёт ТОЛЬКО SHA-256 (64 hex). Сырые id (GUID, серийник
тома) НЕ хранятся и никуда не отправляются — из хеша их не восстановить. ПДн нет.

КРОССПЛАТФОРМЕННО (целевая ОС клиента — Windows; Mac/Linux — для разработки):
    Windows : MachineGuid (реестр HKLM\\SOFTWARE\\Microsoft\\Cryptography) + серийник
              системного тома (vol).
    macOS   : IOPlatformUUID (ioreg).
    Linux   : /etc/machine-id (или /var/lib/dbus/machine-id).
Каждый источник необязателен: берём все доступные, солим и хешируем. Если не удалось
получить НИЧЕГО стабильного — честно возвращаем отпечаток с пометкой weak=True, чтобы
вызывающий мог решить (в фазе 1 — не блокируем).

ГРАНИЦЫ (CONTRACT.md §3): только stdlib, без input(), без секретов в git.
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path

# Соль домена: чтобы отпечаток завода не совпадал с хешами того же железа в чужих системах.
_SALT = b"kontent-zavod/activation/v1"


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _windows_ids() -> list[str]:
    ids: list[str] = []
    # MachineGuid из реестра (стабилен на весь срок жизни установки ОС).
    try:
        import winreg  # только Windows

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
            0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as k:
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            if val:
                ids.append("winguid:" + str(val).strip())
    except (OSError, ImportError):
        pass
    # Серийник системного тома (меняется только при переформатировании диска).
    sysdrive = "C:"
    vol = _run(["cmd", "/c", "vol", sysdrive])
    if vol:
        # Строка вида «... Serial Number is XXXX-XXXX» / «... серийный номер тома: XXXX-XXXX».
        for token in vol.replace("\r", " ").split():
            t = token.strip().strip(":")
            if len(t) == 9 and t[4] == "-" and t.replace("-", "").isalnum():
                ids.append("winvol:" + t.upper())
                break
    return ids


def _macos_ids() -> list[str]:
    out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    for line in out.splitlines():
        if "IOPlatformUUID" in line:
            # ... "IOPlatformUUID" = "XXXXXXXX-...."
            parts = line.split('"')
            if len(parts) >= 4:
                return ["macuuid:" + parts[3].strip()]
    return []


def _linux_ids() -> list[str]:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text(encoding="utf-8").strip()
            if v:
                return ["linuxid:" + v]
        except OSError:
            continue
    return []


def raw_identifiers() -> list[str]:
    """Список доступных стабильных идентификаторов (сырые, НЕ хранить/НЕ слать)."""
    system = platform.system()
    if system == "Windows":
        ids = _windows_ids()
    elif system == "Darwin":
        ids = _macos_ids()
    else:
        ids = _linux_ids()
    return ids


def fingerprint() -> tuple[str, bool]:
    """(hex-отпечаток SHA-256, weak).

    weak=True, если не удалось получить ни одного «сильного» аппаратного id и пришлось
    опереться только на слабые признаки (имя узла/архитектура). В фазе 1 не блокируем,
    но помечаем — чтобы это было видно в статусе.
    """
    ids = raw_identifiers()
    weak = not ids
    if weak:
        # Крайний случай: хоть что-то стабильное в пределах машины.
        ids = ["node:" + platform.node(), "mach:" + platform.machine()]
    h = hashlib.sha256()
    h.update(_SALT)
    for part in sorted(set(ids)):
        h.update(b"\x1f")
        h.update(part.encode("utf-8", "replace"))
    return h.hexdigest(), weak


def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Отпечаток машины (SHA-256, без ПДн).")
    ap.add_argument("--json", action="store_true", help="Машинная сводка.")
    ap.add_argument("--raw", action="store_true",
                    help="Показать сырые id (ОТЛАДКА — не для клиента).")
    args = ap.parse_args(argv)
    fp, weak = fingerprint()
    if args.json:
        out = {"fingerprint": fp, "weak": weak, "system": platform.system()}
        if args.raw:
            out["raw"] = raw_identifiers()
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(fp + ("  (weak)" if weak else ""))
        if args.raw:
            print("raw:", raw_identifiers())
    return 0


if __name__ == "__main__":
    sys.exit(main())
