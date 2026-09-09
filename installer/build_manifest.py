# -*- coding: utf-8 -*-
"""
build_manifest — генератор МАНИФЕСТА ВЕРСИЙ КОМПОНЕНТОВ офлайн-коробки (SFX).

Собирает в один машинный `installer/manifest.json` всё, из чего физически состоит
самораспаковывающийся установщик под Windows: версия встраиваемого Python, полный
запиненный список pip (объединение всех module.manifest.json), веса моделей
(whisper/выравнивание/VAD) с лицензиями, шрифты, системные бинарники, формат SFX и
целевую платформу. Этот файл — источник правды для BUILD-RECIPE.md (что качать и
вендорить на Windows) и для приёмки (сверка «то же, что тестировали»).

Не трогает движок и не качает ничего. Только читает манифесты и печатает JSON.
Только stdlib, кроссплатформенно.

Запуск:
    python installer/build_manifest.py                 # печать в stdout
    python installer/build_manifest.py --out installer/manifest.json
    python installer/build_manifest.py --check         # rc=1, если есть pip без '=='
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

# Целевая платформа офлайн-коробки (клиент Windows без GPU, установка из РФ без VPN).
TARGET = {
    "os": "Windows 10/11 x64",
    "gpu": "не требуется (CPU-only: torch cpu, whisper compute_type=int8)",
    "python_embed": "3.11.9",          # python-3.11.9-embed-amd64 (embeddable)
    "python_abi_tag": "cp311",
    "platform_tag": "win_amd64",
    "wheelhouse": "installer/vendor/wheels",  # куда вендорятся win-колёса (офлайн)
}

# Формат самораспаковки — обоснование одной строкой (см. installer/README.md).
SFX = {
    "format": "Inno Setup 6 (один подписываемый .exe, LZMA2, Pascal-постскрипт)",
    "why": ("офлайн-разворачивание многогигабайтной нагрузки с embeddable-Python и "
            "пост-скриптом (venv/модели) один .exe тянет лучше 7-Zip SFX (нет логики) "
            "и надёжнее NSIS для больших payload'ов"),
    "compiler_note": "iscc.exe (Inno) — сборка ТОЛЬКО на Windows-машине (см. BUILD-RECIPE.md)",
}

# Модели WhisperX (совпадает с install/install_models.py; лицензии — из stages.json).
MODELS = [
    {"role": "whisper ASR (faster-whisper)", "repo": "Systran/faster-whisper-<size>",
     "default_size": "medium (~1.5 ГБ; small ~0.5 ГБ уже в .cache_models dev-машины)",
     "license": "MIT", "into": ".cache_models"},
    {"role": "форс-выравнивание wav2vec2 (ru)",
     "repo": "jonatasgrosman/wav2vec2-large-xlsr-53-russian",
     "size": "~1.2 ГБ", "license": "Apache-2.0", "into": ".cache_models"},
    {"role": "VAD (детектор речи, pyannote via whisperx)",
     "repo": "зеркало whisperx (токен HF не нужен)",
     "size": "~15–20 МБ", "license": "MIT", "into": ".cache_models"},
]

# Гарнитуры, которые РЕАЛЬНО едут в коробке (build_box кладёт всё дерево fonts/).
# Onest — базовая семья движка (engine4.FAMILY), Inter и Manrope — пресеты стиля
# R31 (styles/preset_inter.json, styles/preset_manrope.json). У каждой рядом лежит
# текст OFL: SIL OFL 1.1 требует поставлять лицензию вместе со шрифтом.
FONTS = [
    {"file": "fonts/Onest.ttf", "license": "SIL OFL 1.1",
     "license_file": "fonts/OFL-Onest.txt"},
    {"file": "fonts/Inter/Inter-Variable.ttf", "license": "SIL OFL 1.1",
     "license_file": "fonts/Inter/OFL.txt"},
    {"file": "fonts/Manrope/Manrope-Variable.ttf", "license": "SIL OFL 1.1",
     "license_file": "fonts/Manrope/OFL.txt"},
]


def find_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "installer" else Path.cwd()


def collect_pip_and_bins(root: Path) -> tuple[dict, list, list]:
    """Объединить pip/system_binaries из всех module.manifest.json (canon: spec→модули)."""
    pip: dict[str, list[str]] = {}
    bins: dict[str, dict] = {}
    modules: list[str] = []
    for f in sorted(glob.glob(str(root / "modules" / "*" / "module.manifest.json"))):
        try:
            m = json.loads(Path(f).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        name = m.get("name") or Path(f).parent.name
        modules.append(name)
        for spec in m.get("pip", []) or []:
            pip.setdefault(spec, []).append(name)
        for b in m.get("system_binaries", []) or []:
            if b.get("name"):
                bins[b["name"]] = b
    pip_list = [{"spec": s, "modules": sorted(set(v))} for s, v in sorted(pip.items())]
    return {"count": len(pip_list), "specs": pip_list}, sorted(modules), list(bins.values())


def build(root: Path) -> dict:
    pip, modules, bins = collect_pip_and_bins(root)
    unpinned = [p["spec"] for p in pip["specs"] if "==" not in p["spec"]]
    stages = {}
    sj = root / "install" / "stages.json"
    if sj.is_file():
        try:
            stages = json.loads(sj.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            stages = {}
    return {
        "product": "Контент-завод — офлайн-коробка (SFX для Windows)",
        "schema": 1,
        "target": TARGET,
        "sfx": SFX,
        "stages_version": stages.get("version"),
        "modules": modules,
        "pip": pip,
        "unpinned_pip": unpinned,      # должен быть пуст (CONTRACT.md §3)
        "system_binaries": bins,
        "models": MODELS,
        "fonts": FONTS,
        "third_party_doc": "THIRD_PARTY.md",
        "license_gate": "installer/license_audit.py (GPL/AGPL — стоп)",
        "notes": [
            "Веса моделей и банк музыки в базовый payload НЕ входят как код — "
            "докладываются install_models.py / install_music.py (см. stages.json).",
            "Всё pip строго с '==' (проверяется --check).",
            "Финальная сборка .exe и приёмка распаковки+рендера — на Windows "
            "(BUILD-RECIPE.md); на маке собрано/проверено всё, что проверяемо.",
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Манифест версий компонентов офлайн-коробки.")
    ap.add_argument("--root")
    ap.add_argument("--out", help="Куда записать JSON (иначе — stdout).")
    ap.add_argument("--check", action="store_true",
                    help="rc=1, если в pip есть незапиненные версии.")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    man = build(root)
    text = json.dumps(man, ensure_ascii=False, indent=2)

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text + "\n", encoding="utf-8")
        print(f"Манифест записан: {outp} "
              f"(модулей: {len(man['modules'])}, pip: {man['pip']['count']}).")
    else:
        print(text)

    if args.check and man["unpinned_pip"]:
        print("НАРУШЕНИЕ: незапиненные версии pip: "
              + ", ".join(man["unpinned_pip"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
