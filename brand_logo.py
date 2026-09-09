# -*- coding: utf-8 -*-
"""ФИРМЕННЫЙ ЗНАК НА СЛАЙДАХ КАРУСЕЛИ — файл клиента (НиколаевскТВ).

ЗАЧЕМ. Движки каруселей логотипы не умеют вовсе: ни один из 21 `engine*.py`
и ни один пресет в `styles/` про логотип не знает. Владелец попросил ставить
свой знак на все шаблоны разом (правило 5.4 самопроверки, 8–9 сентября 2026).

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ПРАВКА ДВИЖКА. Слайд сохраняется внутри каждого
движка (`img.save(out)`), таких мест десятки — править их все значит спорить с
каждым обновлением коробки. Здесь логика лежит в файле, которого нет в поставке:
обновление его не затирает. В `modules/content_plan/carousel_bridge.py` добавлен
ТОЛЬКО вызов `stamp_dir(...)` — три строки, их и надо будет вернуть после
обновления (см. раздел 6а в ХЕНДОФФ.md).

ГДЕ ИМЕННО ВЫЗЫВАЕТСЯ. В `carousel_bridge.build()` сразу после того, как движок
отрисовал все слайды, и ДО `checks.verify()` — чтобы проверки видели финальную
картинку, а не промежуточную.

НАСТРОЙКИ (config/factory.config.json, все необязательные):
    carousel_logo         путь к файлу знака (по умолчанию «логотип.png» в корне)
    carousel_logo_scale   доля ширины кадра, 0.02–0.30 (по умолчанию 0.09)
    carousel_logo_margin  отступ от краёв в долях ширины (по умолчанию 0.037)
    carousel_logo_corner  br | bl | tr | tl (по умолчанию br — правый нижний)
    carousel_logo_opacity 0.0–1.0 (по умолчанию 1.0)

Угол по умолчанию правый НИЖНИЙ: сверху справа у части движков стоит счётчик
слайдов («3/8»), слева идёт текст. Правый нижний у всех проверенных семейств
свободен.

Тихий отказ — намеренно: нет файла знака или не читается — слайды остаются как
были, сборка НЕ падает. Логотип это оформление, а не условие выпуска.
"""
from __future__ import annotations

import os
from pathlib import Path

_IMG_EXT = (".png", ".jpg", ".jpeg")

DEFAULT_NAME = "логотип.png"
DEFAULT_SCALE = 0.09
DEFAULT_MARGIN = 0.037
DEFAULT_CORNER = "br"
DEFAULT_OPACITY = 1.0


def _root() -> Path:
    """Корень установки — по тем же маркерам, что у остальных скриптов."""
    env = os.environ.get("FACTORY_ROOT")
    if env and Path(env).exists():
        return Path(env).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here


def _config() -> dict:
    try:
        import json
        p = _root() / "config" / "factory.config.json"
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _num(cfg: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return default if not (lo <= v <= hi) else v


def logo_path() -> Path | None:
    """Файл знака или None. Путь из конфига может быть относительным корню."""
    cfg = _config()
    raw = str(cfg.get("carousel_logo") or DEFAULT_NAME).strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = _root() / raw
    return p if p.is_file() else None


def stamp_image(path, logo=None) -> bool:
    """Поставить знак на ОДИН файл. True — поставили, False — тихо пропустили."""
    try:
        from PIL import Image
    except Exception:
        return False

    src = logo or logo_path()
    if src is None:
        return False

    cfg = _config()
    scale = _num(cfg, "carousel_logo_scale", DEFAULT_SCALE, 0.02, 0.30)
    margin = _num(cfg, "carousel_logo_margin", DEFAULT_MARGIN, 0.0, 0.20)
    opacity = _num(cfg, "carousel_logo_opacity", DEFAULT_OPACITY, 0.0, 1.0)
    corner = str(cfg.get("carousel_logo_corner") or DEFAULT_CORNER).lower()
    if corner not in ("br", "bl", "tr", "tl"):
        corner = DEFAULT_CORNER

    try:
        with Image.open(path) as im:
            base = im.convert("RGBA")
        with Image.open(src) as lg:
            mark = lg.convert("RGBA")

        w = max(1, int(base.width * scale))
        h = max(1, int(round(mark.height * (w / mark.width))))
        mark = mark.resize((w, h), Image.LANCZOS)

        if opacity < 1.0:
            alpha = mark.getchannel("A").point(lambda a: int(a * opacity))
            mark.putalpha(alpha)

        m = int(base.width * margin)
        x = m if corner in ("bl", "tl") else base.width - w - m
        y = m if corner in ("tl", "tr") else base.height - h - m

        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        layer.paste(mark, (x, y), mark)
        out = Image.alpha_composite(base, layer)

        # Сохраняем в исходном формате: png остаётся png, jpg — jpg без альфы.
        ext = str(path).lower()
        if ext.endswith(".png"):
            out.save(path)
        else:
            out.convert("RGB").save(path, quality=95)
        return True
    except Exception:
        return False


def stamp_dir(directory, only=None) -> int:
    """Поставить знак на все кадры папки. Возвращает, сколько проставлено.

    `only` — необязательный список имён файлов (например, конкретные слайды).
    Ошибка на одном кадре не роняет остальные и не роняет сборку.
    """
    d = Path(directory)
    if not d.is_dir():
        return 0
    src = logo_path()
    if src is None:
        return 0

    done = 0
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _IMG_EXT:
            continue
        if only and f.name not in only:
            continue
        if stamp_image(f, logo=src):
            done += 1
    return done


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Поставить фирменный знак на кадры")
    ap.add_argument("directory", help="папка со слайдами")
    a = ap.parse_args()
    src = logo_path()
    if src is None:
        raise SystemExit("Знак не найден. Положите файл в корень завода как "
                         "«логотип.png» или укажите carousel_logo в настройках.")
    print("знак:", src)
    print("проставлено кадров:", stamp_dir(a.directory))
