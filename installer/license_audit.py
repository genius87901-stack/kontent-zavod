# -*- coding: utf-8 -*-
"""
license_audit — механизированный обход РЕАЛЬНЫХ файлов лицензий зависимостей и
СТОП-ГЕЙТ по GPL/AGPL (CONTRACT.md §3 + бриф A1: разрешены только
MIT/BSD/Apache-2.0/SIL OFL/ISC и подобные пермиссивные; GPL/AGPL — запрещены).

ПОЧЕМУ ФАЙЛ, А НЕ ОПИСАНИЕ. Пакет может декларировать «BSD» в поле License, но его
BINARY WHEEL — тащить GPL-библиотеку внутри (реальный случай: pillow-heif бандлит
libx265 → сам wheel становится GPLv2). Поэтому аудит смотрит НА ФАЙЛЫ:
  1) METADATA дистрибутива (License-Expression / Classifier / License);
  2) все вложенные файлы лицензий (LICENSE*, COPYING*, LICENSES_bundled.txt,
     *ThirdParty*Notices*), где производитель wheel сам пишет итоговую лицензию;
  3) вложенные бинарники (*.dll/.so/.dylib) по стоп-списку GPL-библиотек (x265/x264…).

Что считаем БЛОКЕРОМ (rc=1):
  • AGPL где угодно в цепочке;
  • собственная лицензия пакета — GPL-2.0/GPL-3.0 (но НЕ «...with GCC exception»
    и НЕ LGPL);
  • явная фраза «binary wheel(s): GPLvX» в bundled-файле;
  • вложенная GPL нативная библиотека по имени (libx265, libx264, …).
Что помечаем ПРЕДУПРЕЖДЕНИЕМ (rc=0, но видно):
  • LGPL (динамическая линковка обычно допустима для проприетарной поставки —
    решение владельца, но зафиксировать надо);
  • GPL-3.0-with-GCC-exception (numpy тащит libgfortran/libquadmath — GCC Runtime
    Library Exception ЯВНО разрешает распространение с не-GPL софтом).

Вход (любой из):
  --wheelhouse DIR   каталог с *.whl (распакует во временный подкаталог и осмотрит)
  --site DIR         установленный site-packages (осмотрит *.dist-info)
  --extracted DIR    уже распакованные *.dist-info / пакеты

Выход: русская сводка; --json — машинно; rc=0 — блокеров нет; rc=1 — есть GPL/AGPL;
rc=2 — нечего проверять / плохой ввод. Только stdlib, кроссплатформенно.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

# ── Классификация лицензий ────────────────────────────────────────────────────
AGPL_RE = re.compile(r"\bA-?GPL\b|Affero", re.IGNORECASE)
# GPL, но НЕ LGPL и НЕ «with ... exception»
GPL_OWN_RE = re.compile(r"\bGPL(v?[23])?\b", re.IGNORECASE)
LGPL_RE = re.compile(r"\bLGPL\b|Lesser General Public", re.IGNORECASE)
GCC_EXC_RE = re.compile(r"GCC[- ]?exception|Runtime Library Exception|with GCC",
                        re.IGNORECASE)
BINARY_WHEEL_GPL_RE = re.compile(
    r"(binary\s+wheels?|for\s+\S+\s+binary\s+wheels?)[^\n]{0,40}\bA?GPL", re.IGNORECASE)
PERMISSIVE_HINTS = (
    "mit", "bsd", "apache", "isc", "psf", "python software foundation",
    "hpnd", "mit-cmu", "openssl", "zlib", "mpl", "unlicense", "sil open font",
    "ofl", "public domain", "wtfpl", "boost",
)
# Стоп-список нативных библиотек под GPL (имя файла .dll/.so/.dylib).
GPL_NATIVE_LIBS = ("x265", "x264", "libfaac", "libfdk", "libmp3lame_gpl")


def classify_text(blob: str) -> str:
    """Грубая классификация одной порции текста лицензии.

    Возвращает: 'agpl' | 'gpl' | 'gpl-exception' | 'lgpl' | 'permissive' | 'unknown'.
    """
    if AGPL_RE.search(blob):
        return "agpl"
    if BINARY_WHEEL_GPL_RE.search(blob):
        return "gpl"
    has_gpl = bool(GPL_OWN_RE.search(blob))
    if has_gpl and GCC_EXC_RE.search(blob):
        return "gpl-exception"
    if LGPL_RE.search(blob):
        return "lgpl"
    if has_gpl:
        return "gpl"
    low = blob.lower()
    if any(h in low for h in PERMISSIVE_HINTS):
        return "permissive"
    return "unknown"


def _metadata_license(meta_text: str) -> tuple[str, str]:
    """Собственная лицензия пакета из METADATA (человекочитаемо, классификация)."""
    fields = []
    for line in meta_text.splitlines():
        if line.startswith(("License-Expression:", "License:", "Classifier: License")):
            fields.append(line.split(":", 1)[1].strip() if ":" in line else line)
        if line.startswith("Name:") and not fields:
            pass
        if not line.strip():  # конец заголовка METADATA
            break
    declared = "; ".join(f for f in fields if f)[:120] or "(не указана)"
    # для классификации собственной лицензии смотрим ТОЛЬКО поля заголовка,
    # чтобы тело LICENSE (где могут упоминаться чужие лицензии) не давало ложняк
    cls = classify_text(" ".join(fields))
    return declared, cls


# Классы серьёзности → вердикт. BLOCK — то, что реально делает поставку GPL:
# собственная лицензия пакета, вложенная нативная GPL-библиотека, явная фраза
# «binary wheels: GPL». WARN — упоминания GPL/AGPL внутри агрегированных нотисов
# (boilerplate GPLv3/LGPL текст сам называет AGPL — это НЕ значит, что пакет AGPL),
# LGPL (динамическая линковка) и GPL-с-GCC-исключением (распространение разрешено).
_ORDER = {
    "permissive": 0, "unknown": 1,
    "gpl-exception": 2, "lgpl": 3, "gpl-mention": 4, "agpl-mention": 5,
    "gpl-native": 6, "gpl": 7, "agpl": 8,
}
_BLOCK = {"gpl-native", "gpl", "agpl"}
_WARN = {"unknown", "gpl-exception", "lgpl", "gpl-mention", "agpl-mention"}


def audit_distinfo(dist_dir: Path) -> dict:
    """Осмотреть один *.dist-info + рядом лежащий пакет: реальные файлы лицензий."""
    name = dist_dir.name.split("-")[0]
    ver = ""
    m = re.match(r"(.+?)-([0-9][^-]*)\.dist-info$", dist_dir.name)
    if m:
        name, ver = m.group(1), m.group(2)

    findings: list[str] = []
    worst = "permissive"

    def bump(kind: str, why: str):
        nonlocal worst
        if _ORDER.get(kind, 0) > _ORDER.get(worst, 0):
            worst = kind
        findings.append(f"{kind}: {why}")

    declared = "(нет METADATA)"
    meta = dist_dir / "METADATA"
    if meta.is_file():
        text = meta.read_text(encoding="utf-8", errors="replace")
        declared, cls = _metadata_license(text)
        # собственная лицензия пакета — авторитетный сигнал (gpl/agpl → BLOCK)
        bump(cls, f"METADATA License = «{declared}»")

    # реальные файлы лицензий: и в dist-info, и в самом пакете (bundled libs)
    #
    # ВАЖНО, где именно лежат вендоренные нативные библиотеки — от этого зависит,
    # увидит ли гейт GPL:
    #   • macOS (delocate)   → внутри пакета: <pkg>/.dylibs/
    #   • Windows (delvewheel) → РЯДОМ с пакетом: <pkg>.libs/   ← сюда смотреть обязательно
    #   • Linux (auditwheel)  → рядом с пакетом: <pkg>.libs/
    # Проверено на av-15.1.0-cp311-cp311-win_amd64.whl: libx264/libx265 лежат в
    # av.libs/, а не в av/ — без этой строки гейт пропускал GPL-колесо на Windows,
    # хотя на mac тот же пакет ловил (BOX-23).
    root = dist_dir.parent
    stem = name.replace("-", "_")
    cands = [root / stem, root / f"{stem}.libs", root / f"{name}.libs",
             root / stem / ".dylibs"]
    scan_dirs = [dist_dir] + [d for d in cands if d.is_dir()]
    for base in scan_dirs:
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            nm = p.name.lower()
            # 1) вложенные нативные бинарники — по стоп-списку GPL (реальная линковка)
            if p.suffix.lower() in (".dll", ".so", ".dylib") or ".so." in nm:
                for bad in GPL_NATIVE_LIBS:
                    if bad in nm:
                        bump("gpl-native", f"вложена GPL-библиотека {p.name}")
                continue
            # 2) файлы лицензий / нотисы — текст
            if re.search(r"license|copying|notice|bundled", nm):
                try:
                    blob = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = p.relative_to(base)
                # явная фраза «для binary wheels лицензия GPL» — это BLOCK
                if BINARY_WHEEL_GPL_RE.search(blob):
                    bump("gpl", f"в {rel}: заявлено «binary wheel = GPL»")
                    continue
                kind = classify_text(blob)
                # прочие упоминания в агрегированных файлах — только WARN, не BLOCK:
                # boilerplate GPLv3/LGPL сам называет AGPL/GPL, это не лицензия пакета
                if kind == "agpl":
                    bump("agpl-mention", f"в {rel} встречается текст AGPL (проверить, "
                                         f"не залинкован ли AGPL-компонент)")
                elif kind == "gpl":
                    bump("gpl-mention", f"в {rel} встречается текст GPL (вероятно "
                                        f"boilerplate/нотисы — проверить)")
                elif kind in ("lgpl", "gpl-exception"):
                    bump(kind, f"в {rel}: {kind}")

    verdict = "BLOCK" if worst in _BLOCK else ("WARN" if worst in _WARN else "OK")
    return {"name": name, "version": ver, "declared": declared,
            "class": worst, "verdict": verdict, "findings": findings}


def collect_distinfos(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.dist-info") if p.is_dir())


def from_wheelhouse(wheelhouse: Path, workdir: Path) -> Path:
    n = 0
    for whl in sorted(wheelhouse.glob("*.whl")):
        try:
            with zipfile.ZipFile(whl) as z:
                z.extractall(workdir / whl.stem)
            n += 1
        except zipfile.BadZipFile:
            pass
    return workdir


def run(reports_from: Path) -> list[dict]:
    out = []
    for di in collect_distinfos(reports_from):
        out.append(audit_distinfo(di))
    # схлопнуть дубли (один пакет — одна строка, худший вердикт)
    best: dict[str, dict] = {}
    rank = {"OK": 0, "WARN": 1, "BLOCK": 2}
    for r in out:
        key = r["name"].lower()
        if key not in best or rank[r["verdict"]] > rank[best[key]["verdict"]]:
            best[key] = r
    return sorted(best.values(), key=lambda r: (-rank[r["verdict"]], r["name"].lower()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Аудит лицензий зависимостей (GPL/AGPL-гейт).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--wheelhouse", help="Каталог с *.whl.")
    g.add_argument("--site", help="Установленный site-packages.")
    g.add_argument("--extracted", help="Уже распакованные пакеты/dist-info.")
    ap.add_argument("--json", action="store_true", help="Машинная сводка.")
    args = ap.parse_args(argv)

    # Распакованные колёса живут ТОЛЬКО на время разбора: 134 колеса дают ~2.5 ГБ
    # во временной папке, и без уборки каждый прогон гейта (а его зовут и build_box,
    # и preflight) оставлял их навсегда — за сессию сборки диск съедало на 15 ГБ.
    tmp = None
    try:
        if args.wheelhouse:
            src = Path(args.wheelhouse)
            if not src.is_dir():
                print(f"Нет каталога: {src}"); return 2
            tmp = Path(tempfile.mkdtemp(prefix="licaudit_"))
            scan_root = from_wheelhouse(src, tmp)
        else:
            scan_root = Path(args.site or args.extracted)
            if not scan_root.is_dir():
                print(f"Нет каталога: {scan_root}"); return 2

        rows = run(scan_root)          # дальше нужны только разобранные строки
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    if not rows:
        print("Не найдено ни одного *.dist-info для проверки.")
        return 2

    blockers = [r for r in rows if r["verdict"] == "BLOCK"]
    warns = [r for r in rows if r["verdict"] == "WARN"]

    if args.json:
        print(json.dumps({"total": len(rows), "blockers": len(blockers),
                          "warnings": len(warns), "packages": rows},
                         ensure_ascii=False, indent=2))
        return 1 if blockers else 0

    print(f"Проверено пакетов: {len(rows)}. Блокеров: {len(blockers)}, "
          f"предупреждений: {len(warns)}.\n")
    if blockers:
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("!!!  GPL/AGPL В ЦЕПОЧКЕ — ПОСТАВКА ЗАПРЕЩЕНА (CONTRACT.md §3)  !!!")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        for r in blockers:
            print(f"  ✗ {r['name']}=={r['version']}  [{r['class'].upper()}]  «{r['declared']}»")
            for f in r["findings"]:
                if f.split(":")[0] in _BLOCK:
                    print(f"        └ {f}")
        print()
    if warns:
        print("Предупреждения (не блокеры, но зафиксировать — решение владельца):")
        for r in warns:
            print(f"  • {r['name']}=={r['version']}  [{r['class']}]  «{r['declared']}»")
        print()
    print("OK-пакетов (пермиссивные): " +
          ", ".join(f"{r['name']}" for r in rows if r["verdict"] == "OK") or "—")

    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
