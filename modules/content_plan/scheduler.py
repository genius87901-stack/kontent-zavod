# -*- coding: utf-8 -*-
"""Планировщик-цикл: раз в интервал прогоняет исполнитель + гейт по плану.

По умолчанию БЕЗОПАСНО: --once (один прогон) и dry-run (executor не тратит API,
gate не публикует — жёсткий стоп в gate.py). --loop гоняет цикл; на реальную
отправку нужен ещё --arm И конфиг content_plan_live_publish (в прототипе всё
равно упрётся в жёсткий стоп).

РАСПИСАНИЕ В КОРОБКЕ (не в этом файле — это слой установки, OWNER):
  * Windows (целевая ОС клиента): Планировщик заданий →
      python -m content_plan.scheduler --once
    раз в N минут. Свой демон/службу модуль не создаёт.
  * Мак разработки: launchd, как sync_daemon.

Запуск вручную:
    python -m content_plan.scheduler --once            # один безопасный прогон
    python -m content_plan.scheduler --loop --interval 300
    python -m content_plan.scheduler --once --arm      # вооружить (упрётся в стоп)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import config, executor, gate, model, paths


def run_once(now: datetime = None, armed: bool = False, lead_days: int = None):
    now = now or datetime.now()
    plan = model.load_plan()

    # Реальная сборка черновиков — только под env CONTENT_PLAN_REAL_GEN.
    # Дефолт (флаг не задан) — dry-run: движки не зовём, API не тратим. Это про
    # ГЕНЕРАЦИЮ; предохранитель ПУБЛИКАЦИИ (gate) не трогается ни при каком флаге.
    dry_run = not executor.real_gen_enabled()
    ex_kwargs = {"now": now, "dry_run": dry_run}
    if lead_days is not None:
        ex_kwargs["lead_days"] = lead_days
    ex_report = executor.run(plan, **ex_kwargs)
    gate_report = gate.run(plan, now=now, armed=armed)
    model.save_plan(plan)

    return {"now": now.replace(microsecond=0).isoformat(),
            "real_gen": not dry_run,
            "executor": ex_report, "gate": gate_report,
            "counts": model.counts(plan)}


def _print_report(rep: dict):
    print("── прогон %s ──" % rep["now"])
    print("  режим генерации: %s" % ("РЕАЛЬНАЯ сборка (CONTENT_PLAN_REAL_GEN)"
          if rep.get("real_gen") else "dry-run (движки не зовутся)"))
    ex = rep["executor"]
    if ex.get("skipped_not_approved"):
        print("  исполнитель: план НЕ утверждён — генерация пропущена "
              "(нет слова человека на период).")
    else:
        print("  исполнитель: созрело=%d, сгенерено=%s, ошибок=%d"
              % (ex.get("due", 0), ex.get("generated") or "—",
                 len(ex.get("errors") or [])))
    g = rep["gate"]
    print("  окно-вето открыто у: %s" % (g.get("veto_open") or "—"))
    if g.get("trial_reels"):
        print("  ПРОБНЫЙ показ Instagram (%s): %s"
              % (config.trial_reels_mode(), g["trial_reels"]))
    if g.get("auto_approved"):
        print("  авто-одобрено (режим %s): %s"
              % (g.get("approval_mode"), g["auto_approved"]))
    if g.get("held_manual"):
        print("  ждут ручного «ок» (режим daily): %s" % g["held_manual"])
    if g.get("awaiting_approval"):
        print("  ВРЕМЯ ВЫШЛО, но выпуск не утверждён — не уйдёт: %s"
              % g["awaiting_approval"])
    if g.get("held"):
        print("  УДЕРЖАНО (ничего не ушло):")
        for h in g["held"]:
            print("    - %s: %s" % (h["slot"], "; ".join(h["reasons"])))
            print("      команда, которая ушла бы: %s" % h["cmd"])
    print("  статусы: %s" % (rep["counts"] or "—"))
    print("  доска-вето: %s" % paths.veto_board_path())


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Цикл автономного исполнителя + гейт с окном-вето.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="один прогон (по умолчанию)")
    mode.add_argument("--loop", action="store_true",
                      help="крутить цикл до Ctrl+C")
    ap.add_argument("--interval", type=int, default=300,
                    help="интервал цикла в секундах (для --loop), по умолчанию 300")
    ap.add_argument("--arm", action="store_true",
                    help="вооружить реальную отправку (в прототипе упрётся в "
                         "жёсткий стоп gate.REAL_PUBLISH_IMPLEMENTED)")
    ap.add_argument("--lead-days", type=int, default=None,
                    help="за сколько дней до публикации генерить слот")
    ap.add_argument("--now", default=None, metavar="YYYY-MM-DDTHH:MM:SS",
                    help="считать это время «сейчас» — прогнать план вперёд и "
                         "посмотреть, что произойдёт (только с --once; "
                         "публикации всё равно нет — жёсткий стоп)")
    a = ap.parse_args(argv)

    now = None
    if a.now:
        try:
            now = datetime.strptime(a.now, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            print("Не понял --now %r: нужен формат YYYY-MM-DDTHH:MM:SS" % a.now)
            return 1
        if a.loop:
            print("--now работает только с одиночным прогоном (--once).")
            return 1

    if a.arm:
        print("[ВНИМАНИЕ] --arm выставлен, но в прототипе стоит жёсткий стоп — "
              "реальной публикации НЕ будет.")

    if a.loop:
        print("Цикл запущен, интервал %d c. Ctrl+C — остановить." % a.interval)
        try:
            while True:
                _print_report(run_once(armed=a.arm, lead_days=a.lead_days))
                time.sleep(max(5, a.interval))
        except KeyboardInterrupt:
            print("\nОстановлено.")
            return 0
    else:
        _print_report(run_once(now=now, armed=a.arm, lead_days=a.lead_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
