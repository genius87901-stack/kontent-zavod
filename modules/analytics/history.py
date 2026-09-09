# -*- coding: utf-8 -*-
"""ИСТОРИЯ замеров метрик — чтобы видеть динамику, а не только срез «сейчас».

`model.py` держит СРЕЗ: одна запись на пост, каждый новый `ingest` её
перезаписывает (upsert по post_id). Этого хватает для правил «хорошо/плохо», но
не хватает для вопросов «пост ещё набирает или уже встал?», «за сутки прибавил
или замер?».

Поэтому каждый успешный замер дополнительно ДОПИСЫВАЕТСЯ сюда — по строке JSON на
(пост × момент замера), формат JSONL:

    {"at": "2026-08-02T10:00:00+00:00", "post_id": "c38_instagram",
     "platform": "instagram", "source": "upload_post",
     "captured_at": "…", "metrics": {…}, "derived": {…}}

Почему JSONL, а не sqlite: модуль обязан оставаться на чистом stdlib и переживать
обрыв записи. Дописывание строки атомарно на практике (один короткий write),
битая строка при чтении пропускается — история одного сбоя не теряется целиком.
Файл лежит в гитигнорируемой папке данных: метрики аккаунта клиента в репозиторий
не уезжают.

Дедупликация: один и тот же снимок (тот же пост с теми же значениями метрик) два
раза подряд не пишем — иначе ежечасный прогон надует файл нулевой динамикой.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import model, paths


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read(post_id: str = None, limit: int = None) -> list:
    """Читает историю (опционально по одному посту). Битые строки пропускаются,
    нет файла → []. Никогда не поднимает исключений — история не критична."""
    path = paths.history_path()
    if not path.exists():
        return []
    out = []
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if post_id and rec.get("post_id") != post_id:
                    continue
                out.append(rec)
    except OSError:
        return []
    if limit:
        return out[-int(limit):]
    return out


def _same_metrics(a, b) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return all(a.get(k) == b.get(k) for k in model.METRIC_FIELDS)


def last_for(post_id: str):
    """Последний записанный замер поста или None."""
    rows = read(post_id=post_id)
    return rows[-1] if rows else None


def append(record: dict, captured_at: str = None) -> bool:
    """Дописать замер в историю. Возвращает True, если строка реально записана.

    Не пишет, если предыдущий замер этого поста нёс РОВНО те же метрики — так
    ежечасный прогон не раздувает файл нулевой динамикой. Ошибку записи глотаем:
    история — приятное дополнение, из-за неё сбор метрик падать не должен."""
    if not isinstance(record, dict) or not record.get("post_id"):
        return False
    metrics = record.get("metrics") or {}
    prev = last_for(record["post_id"])
    if prev is not None and _same_metrics(prev.get("metrics"), metrics):
        return False
    row = {
        "at": _now(),
        "post_id": record.get("post_id"),
        "platform": record.get("platform"),
        "format": record.get("format"),
        "source": record.get("source"),
        "captured_at": captured_at or record.get("captured_at") or "",
        "metrics": {k: metrics.get(k) for k in model.METRIC_FIELDS},
        "derived": record.get("derived") or {},
    }
    paths.ensure_dirs()
    try:
        with paths.history_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def append_many(records) -> int:
    return sum(1 for rec in records if append(rec))


# --- динамика ----------------------------------------------------------------

def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _hours_between(a, b):
    try:
        da, db = datetime.fromisoformat(str(a)), datetime.fromisoformat(str(b))
    except ValueError:
        return None
    if da.tzinfo is None:
        da = da.replace(tzinfo=timezone.utc)
    if db.tzinfo is None:
        db = db.replace(tzinfo=timezone.utc)
    return round((db - da).total_seconds() / 3600.0, 2)


def delta(post_id: str, field: str = None) -> dict:
    """Насколько пост прибавил между ПЕРВЫМ и ПОСЛЕДНИМ замером.

    Возвращает {"ok": False, "reason": …}, пока замер всего один — это честное
    «динамики ещё нет», а не ноль. `field` (например "views") сужает до одной
    метрики; без него считаем по базовой аудитории (охват→показы→просмотры)."""
    rows = read(post_id=post_id)
    if len(rows) < 2:
        return {"ok": False, "post_id": post_id,
                "reason": "замер пока один — динамику покажем со второго "
                          "снятия метрик"}
    first, last = rows[0], rows[-1]

    def value(row):
        if field:
            return _num((row.get("metrics") or {}).get(field))
        return _num((row.get("derived") or {}).get("base_audience"))

    v0, v1 = value(first), value(last)
    hours = _hours_between(first.get("at"), last.get("at"))
    out = {"ok": True, "post_id": post_id, "field": field or "base_audience",
           "first": v0, "last": v1, "measurements": len(rows),
           "from": first.get("at"), "to": last.get("at"), "hours": hours}
    if v0 is None or v1 is None:
        out["growth"] = None
        out["growth_pct"] = None
        out["per_day"] = None
        return out
    out["growth"] = v1 - v0
    out["growth_pct"] = round((v1 - v0) / v0 * 100.0, 2) if v0 else None
    out["per_day"] = round((v1 - v0) / hours * 24.0, 1) if hours else None
    return out


def summary() -> dict:
    """Сводка по истории для doctor/cli: сколько замеров, по скольким постам,
    когда первый и последний."""
    rows = read()
    if not rows:
        return {"measurements": 0, "posts": 0, "first": None, "last": None}
    return {"measurements": len(rows),
            "posts": len({r.get("post_id") for r in rows}),
            "first": rows[0].get("at"), "last": rows[-1].get("at")}
