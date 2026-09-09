# -*- coding: utf-8 -*-
"""Схема контент-плана, чтение/запись, статусы слотов и их переходы.

Единственное место, где меняется статус слота — здесь (add_history + set_status),
чтобы любой переход был зафиксирован в history и нельзя было проскочить мимо
предохранителя.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import paths

SCHEMA_VERSION = 1

# Статусы слота и разрешённые переходы. Всё, что не в ALLOWED — запрещено.
#
# Жизненный цикл (продуктовыми словами):
#   planned    — ЧЕРНОВИК: слот в плане, контент ещё не собран;
#   generated  — НА УТВЕРЖДЕНИИ: контент собран, открыто окно-вето, клиент видит;
#   showcased  — В ВИТРИНЕ: единица выложена в личный телеграм-канал клиента и
#                ждёт, пока он её посмотрит глазами с телефона (BOX-26);
#   revising   — ЕСТЬ ПРАВКИ: клиент словами сказал, что переделать. Такой слот
#                НЕ одобряется ни вручную, ни авто — сначала переделка;
#   approved   — УТВЕРЖДЁН: клиент сказал «да» этому конкретному выпуску
#                (в режиме daily — руками, в горизонтных режимах — авто-одобрение
#                по истечении окна-вето). Публикации ещё НЕ было;
#   published  — ОПУБЛИКОВАН (в прототипе недостижим: жёсткий стоп в gate.py);
#   vetoed     — СНЯТ клиентом;
#   failed     — ошибка сборки.
PLANNED = "planned"
GENERATED = "generated"
SHOWCASED = "showcased"
REVISING = "revising"
APPROVED = "approved"
PUBLISHED = "published"
VETOED = "vetoed"
FAILED = "failed"

# Человекочитаемые подписи статусов — для таблиц и доски-вето.
STATUS_LABEL = {
    PLANNED: "черновик",
    GENERATED: "на утверждении",
    SHOWCASED: "в витрине, ждёт просмотра",
    REVISING: "есть правки от клиента",
    APPROVED: "утверждён",
    PUBLISHED: "опубликован",
    VETOED: "снят",
    FAILED: "ошибка",
}

ALLOWED = {
    PLANNED: {GENERATED, VETOED, FAILED},
    # GENERATED: собранный черновик. Штатный путь BOX-26 — сначала в витрину
    # (SHOWCASED), но прямое одобрение оставлено: витрина может быть недоступна
    # (офлайн-клиент), и тогда клиент утверждает по таблице, как раньше.
    GENERATED: {SHOWCASED, REVISING, APPROVED, VETOED, PLANNED, FAILED},
    # SHOWCASED: клиент видит единицу в своём канале. Отсюда три исхода —
    # «ок» (APPROVED), «переделать» (REVISING), «убрать» (VETOED).
    SHOWCASED: {APPROVED, REVISING, VETOED, PLANNED, FAILED},
    # REVISING: правка записана. PLANNED = перегенерить с нуля;
    # GENERATED = черновик поправлен и готов к повторной выкладке в витрину.
    REVISING: {PLANNED, GENERATED, SHOWCASED, VETOED, FAILED},
    APPROVED: {PUBLISHED, VETOED, PLANNED, REVISING, FAILED},  # снять/поправить
    FAILED: {PLANNED, VETOED},
    VETOED: {PLANNED},                                  # можно вернуть в план
    PUBLISHED: set(),                                   # финал
}

# Слоты, которые ждут решения человека (витрина показана или ещё нет). Гейт
# и витрина обращаются к этому кортежу, а не перечисляют статусы руками.
PENDING_APPROVAL = (GENERATED, SHOWCASED)

# Статусы, при которых наружу ещё ничего не ушло (для доктора-предохранителя).
UNPUBLISHED = (PLANNED, GENERATED, SHOWCASED, REVISING, APPROVED, VETOED, FAILED)


def label(status: str) -> str:
    return STATUS_LABEL.get(status, status or "?")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# --- загрузка / сохранение -------------------------------------------------

def empty_plan() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "created": now_iso(),
        "approved": False,
        "approved_at": None,
        "approved_by": None,
        "brief": {},
        "slots": [],
    }


def load_plan(path: Path = None) -> dict:
    path = path or paths.plan_path()
    if not path.exists():
        return empty_plan()
    with path.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("content_plan.json повреждён: ожидался объект плана.")
    data.setdefault("slots", [])
    data.setdefault("brief", {})
    data.setdefault("approved", False)
    return data


def save_plan(plan: dict, path: Path = None) -> None:
    path = path or paths.plan_path()
    paths.ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)  # атомарная замена — не оставить полуфайл


# --- работа со слотами -----------------------------------------------------

def find_slot(plan: dict, slot_id: str):
    for slot in plan.get("slots", []):
        if slot.get("id") == slot_id:
            return slot
    return None


def add_history(slot: dict, event: str, note: str = "") -> None:
    slot.setdefault("history", []).append(
        {"at": now_iso(), "event": event, "note": note})


def set_status(slot: dict, new_status: str, note: str = "") -> None:
    """Единственный легальный способ сменить статус. Проверяет переход."""
    old = slot.get("status", PLANNED)
    if new_status == old:
        return
    allowed = ALLOWED.get(old, set())
    if new_status not in allowed:
        raise ValueError(
            "Недопустимый переход слота %s: %s -> %s (можно: %s)"
            % (slot.get("id"), old, new_status, ", ".join(sorted(allowed)) or "—"))
    slot["status"] = new_status
    add_history(slot, "status:%s->%s" % (old, new_status), note)


def is_approved(plan: dict) -> bool:
    return bool(plan.get("approved"))


def approve(plan: dict, who: str = "клиент") -> None:
    plan["approved"] = True
    plan["approved_at"] = now_iso()
    plan["approved_by"] = who


def revoke_approval(plan: dict) -> None:
    plan["approved"] = False
    plan["approved_at"] = None
    plan["approved_by"] = None


# --- счётчики для doctor/status --------------------------------------------

def counts(plan: dict) -> dict:
    out = {}
    for slot in plan.get("slots", []):
        st = slot.get("status", PLANNED)
        out[st] = out.get(st, 0) + 1
    return out
