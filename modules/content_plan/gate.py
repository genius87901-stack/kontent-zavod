# -*- coding: utf-8 -*-
"""Гейт публикации с окном-вето.

Две задачи:
  1. Открыть окно-вето — показать клиенту готовые слоты заранее (veto_board.md),
     чтобы он успел снять/поправить плохой пост до времени публикации.
  2. На время публикации — провести слот через ПЯТЬ ЗАМКОВ и штатный путь autopost.

ПРЕДОХРАНИТЕЛЬ (главный закон проекта — не обходить):
  * Реальной публикации в прототипе НЕТ. Стоит жёсткий стоп REAL_PUBLISH_IMPLEMENTED.
  * Публикация возможна только тем же путём, что требует явного разрешения:
    гейт СОБИРАЕТ команду autopost.py, а не пишет свой постинг. Настоящая отправка
    прошла бы через штатный autopost `--send` (существующий предохранитель),
    который гейт НЕ отключает.
  * Автономное «разрешение» = утверждённый план + истёкшее окно-вето без снятия,
    а НЕ выключенный флаг.
"""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timedelta

from . import config, executor, model, paths

# ЖЁСТКИЙ СТОП ПРОТОТИПА. Пока False — гейт НИКОГДА не вызывает реальную отправку,
# только печатает и логирует команду, которая ушла бы. Снятие — отдельная задача
# интеграции с владельцем autopost.py (ветка publishing), вместе с проверкой,
# что штатный --send-предохранитель остаётся на месте.
REAL_PUBLISH_IMPLEMENTED = False


def _now(now=None) -> datetime:
    return now or datetime.now()


def _parse(dt: str):
    try:
        return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


# --- окно-вето -------------------------------------------------------------

def in_veto_window(slot: dict, now: datetime) -> bool:
    """Окно открыто: слот собран (на утверждении, показан в витрине или уже
    утверждён), наступило veto_opens_at, но ещё не время публикации.
    Утверждённый слот тоже остаётся в окне: клиент вправе передумать и снять его
    до самого выхода."""
    if slot.get("status") not in model.PENDING_APPROVAL + (model.APPROVED,):
        return False
    opens = _parse(slot.get("veto_opens_at"))
    publish = _parse(slot.get("publish_at"))
    if not opens or not publish:
        return False
    return opens <= now < publish


def veto(plan: dict, slot_id: str, note: str = "снято клиентом") -> bool:
    """Клиент снимает слот — он не уйдёт. Возможно, пока не опубликован."""
    slot = model.find_slot(plan, slot_id)
    if slot is None:
        return False
    if slot.get("status") == model.PUBLISHED:
        return False
    model.set_status(slot, model.VETOED, note)
    return True


def approve_slot(plan: dict, slot_id: str, who: str = "клиент",
                 note: str = "") -> bool:
    """Явное утверждение КОНКРЕТНОГО выпуска человеком (режим daily и любой
    другой). Переводит слот «на утверждении» / «в витрине» → «утверждён».
    Публикацию НЕ запускает: поверх стоит жёсткий стоп, а гейт всё равно ждёт
    времени выхода. Слот с незакрытыми правками (revising) не утверждается —
    сначала переделка."""
    slot = model.find_slot(plan, slot_id)
    if slot is None or slot.get("status") not in model.PENDING_APPROVAL:
        return False
    model.set_status(slot, model.APPROVED,
                     note or "утверждён вручную: %s" % who)
    slot["approved_by"] = who
    slot["approved_at"] = model.now_iso()
    return True


def write_veto_board(plan: dict, now: datetime = None) -> str:
    """Человекочитаемая доска «что скоро уйдёт — можно отменить». В проде это
    ушло бы в Telegram-бот; в прототипе — файл veto_board.md."""
    now = _now(now)
    paths.ensure_dirs()
    open_slots = [s for s in plan.get("slots", []) if in_veto_window(s, now)]
    lines = ["# Окно-вето: что скоро опубликуется",
             "",
             "_Обновлено: %s_" % model.now_iso(),
             "",
             "Пост уйдёт по расписанию, если ты его не снимешь. Чтобы отменить — "
             "скажи: сними слот `<id>`.",
             ""]
    if not open_slots:
        lines.append("Сейчас в окне-вето ничего нет.")
    else:
        for s in open_slots:
            lines.append("## %s — %s" % (s["publish_at"], s["topic"]))
            lines.append("- id: `%s`" % s["id"])
            lines.append("- статус: %s%s" % (
                model.label(s.get("status")),
                (" (кем: %s)" % s["approved_by"]) if s.get("approved_by") else ""))
            lines.append("- формат: %s | рубрика: %s" % (s["format"], s["rubric"]))
            lines.append("- площадки: %s" % ", ".join(s.get("platforms", [])))
            if s.get("hook"):
                lines.append("- крючок: %s" % s["hook"])
            if s.get("showcase"):
                lines.append("- в витрине с %s (канал %s)"
                             % (s["showcase"].get("at"),
                                s["showcase"].get("chat")))
            if s.get("revisions"):
                lines.append("- правки клиента: %d" % len(s["revisions"]))
            lines.append("")
    text = "\n".join(lines)
    paths.veto_board_path().write_text(text, encoding="utf-8")
    return text


# --- режим одобрения (пред-разрешение ПОВЕРХ жёсткого стопа) ----------------
#
# Режим НЕ вооружает публикацию. Он управляет ТОЛЬКО тем, как принимается
# решение об одобрении слота (окно-вето / авто-одобрение). Даже «авто на месяц»
# = система авто-ОДОБРЯЕТ слоты, но реальной отправки нет: жёсткий стоп в run()
# держит любой слот. Режим читается через config.py, не хардкодится.

APPROVE_MANUAL = "manual"   # daily: держим до явного одобрения по каждому выпуску
APPROVE_AUTO = "auto"       # горизонт: авто-одобрен (в пределах N дней от «сейчас»)
APPROVE_FAR = "far"         # горизонт: за горизонтом — придержан до приближения


def plan_mode(plan: dict) -> str:
    """Режим одобрения ЭТОГО плана. Приоритет — ответ клиента в брифе (он его
    дал на интервью под этот период); если там пусто или мусор — общий конфиг.
    Так один утверждённый план не меняет режим, когда правят настройки завода."""
    raw = (plan or {}).get("brief", {}).get("publish_approval_mode")
    if isinstance(raw, str) and raw.strip().lower() in config.APPROVAL_MODES:
        return raw.strip().lower()
    return config.publish_approval_mode()


def _within_horizon(slot: dict, now: datetime, horizon_days: int) -> bool:
    """Слот в пределах горизонта авто-одобрения: publish_at <= now + horizon.
    Горизонт считается ОТ «сейчас» в днях (утверждённая формулировка задачи)."""
    publish = _parse(slot.get("publish_at"))
    if publish is None:
        return False
    return publish <= now + timedelta(days=horizon_days)


def approval_decision(slot: dict, now: datetime, mode: str = None,
                      horizon_days: int = None):
    """Решение об одобрении слота под жёстким стопом. Возвращает (decision, причина).
    Ничего не публикует — это пред-разрешение (окно-вето / авто), а не отправка.

      * daily            → APPROVE_MANUAL: держим до явного «ок» по каждому посту
                           (строже всего).
      * горизонт, слот в пределах N дней, не снят → APPROVE_AUTO: авто-одобрен
        (окно-вето всё равно показано, вето можно снять — тогда слот выпадет).
      * горизонт, слот за горизонтом → APPROVE_FAR: придержан до приближения.

    Даже APPROVE_AUTO НЕ означает публикацию: run() держит слот жёстким стопом
    (REAL_PUBLISH_IMPLEMENTED=False) — наружу не уходит ничего ни в одном режиме.
    """
    if mode is None:
        mode = config.publish_approval_mode()
    if horizon_days is None:
        horizon_days = config.approval_horizon_days(mode)

    if slot.get("status") == model.VETOED:
        return APPROVE_FAR, "слот снят клиентом (vetoed) — не одобряется"

    # Правки клиента бьют режим одобрения: даже «авто на месяц» не имеет права
    # пропустить единицу, про которую человек сказал «переделай».
    if slot.get("status") == model.REVISING:
        return (APPROVE_FAR,
                "по слоту есть правки от клиента — не одобряется, пока не "
                "переделан (revise-done)")

    if mode == "daily" or horizon_days <= 0:
        return (APPROVE_MANUAL,
                "режим daily: каждый выпуск подтверждается вручную "
                "(держим до явного «ок»)")

    if _within_horizon(slot, now, horizon_days):
        return (APPROVE_AUTO,
                "режим %s: авто-одобрен в пределах %d дн (окно-вето показано, "
                "вето ещё можно снять)" % (mode, horizon_days))
    return (APPROVE_FAR,
            "режим %s: слот за горизонтом %d дн — придержан до приближения"
            % (mode, horizon_days))


# --- путь публикации (через штатный autopost) ------------------------------

def _load_artifact(slot: dict):
    """Материал слота из очереди content_plan/queue (или None, если его нет).

    Ничего не чинит и не создаёт: только читает то, что положил исполнитель.
    Битый/отсутствующий файл — это None, а не исключение: решение о том, можно
    ли с таким слотом идти дальше, принимает material_ready()."""
    name = slot.get("artifact")
    if not name:
        return None
    try:
        with (paths.queue_dir() / str(name)).open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _artifact_target(slot: dict):
    """Что подставится в autopost: НАСТОЯЩИЙ путь к собранному материалу.

    Путь берём из очереди (content_plan/queue/<slot_id>/…) относительно корня
    установки — оттуда же autopost запускается руками. Если материала ещё нет
    (dry-run, слот не собран, доктор проверяет форму команды), возвращаем
    ВИДИМЫЙ плейсхолдер в угловых скобках: его нельзя перепутать с реальным
    путём, а публиковать такой слот всё равно не даст пятый замок."""
    art = _load_artifact(slot) or {}
    media = art.get("media") if not art.get("dry_run") else None
    base = "%s/%s" % (paths.QUEUE_DIR, slot["id"])
    if isinstance(media, dict):
        if media.get("kind") == "reels" and media.get("video"):
            return "--video", "%s/%s" % (base, media["video"])
        if media.get("kind") == "carousel":
            return "--dir", base
        if media.get("kind") == "story" and media.get("story_kind") == "video"                 and media.get("file"):
            return "--media", str(media["file"])
        if media.get("kind") == "story" and media.get("files"):
            return "--media", "%s/%s" % (base, media["files"][0])
        if media.get("kind") == "story" and media.get("file"):
            return "--media", "%s/%s" % (base, media["file"])
    if slot.get("format") == "reels":
        return "--video", "out_reels/<%s>.mp4" % slot["id"]
    if slot.get("format") == "stories":
        return "--media", "output/stories/<%s>.png" % slot["id"]
    return "--dir", "out_slides/<%s>" % slot["id"]


def _iso_week(dt: datetime):
    y, w, _ = dt.isocalendar()
    return (y, w)


def compute_trial_slots(plan: dict) -> set:
    """ID слотов-Reels, которым в ЭТОМ прогоне назначен пробный показ Instagram
    (BOX-обновление 14.08.2026, autopost --trial). Настройки клиента — три
    вопроса распаковки: trial_reels_mode/_per_week/_cadence (questions.py).

    off или per_week<=0 -> пусто, никакой --trial никогда не добавляется.
    cadence=every  -> один лимит на ВЕСЬ горизонт плана (не обновляется по неделям).
    cadence=weekly / new_format_only -> лимит СВОЙ на каждую ISO-неделю; при
    new_format_only детектор «формат/тема новые» не реализован — прототип
    честно ведёт себя как weekly (см. config.trial_reels_cadence)."""
    if config.trial_reels_mode() == "off":
        return set()
    per_week = config.trial_reels_per_week()
    if per_week <= 0:
        return set()
    cadence = config.trial_reels_cadence()

    reels = sorted(
        (s for s in plan.get("slots", [])
         if s.get("format") == "reels" and _parse(s.get("publish_at"))),
        key=lambda s: _parse(s["publish_at"]))

    if cadence == "every":
        return {s["id"] for s in reels[:per_week]}

    counts, chosen = {}, set()
    for s in reels:
        wk = _iso_week(_parse(s["publish_at"]))
        if counts.get(wk, 0) < per_week:
            chosen.add(s["id"])
            counts[wk] = counts.get(wk, 0) + 1
    return chosen


def _build_story_command(slot: dict) -> str:
    """Команда выкладки СТОРИС — story_publisher, а не autopost.py.

    Два отличия от ленты, которые нельзя потерять:
      * модуль живёт в modules/, поэтому в команде есть `PYTHONPATH=modules` —
        ровно та форма, что записана в клиентском CLAUDE.md;
      * ВРЕМЕНИ ПУБЛИКАЦИИ У СТОРИС НЕТ. `story_publisher` выкладывает сейчас,
        отложенной постановки он не умеет вовсе, поэтому `--at` не дописываем.
        Приписать слоту срок, которого скрипт не исполнит, — та же неправда,
        что и «движка сторис нет»: человек прочитает команду, поверит сроку и
        получит историю не тогда, когда планировал.
    """
    plats = ",".join(slot.get("platforms", []) or ["instagram"])
    parts = ["python", "-m", "story_publisher"] + _story_media_args(slot) + [
        "--to", plats, "--send"]
    return "PYTHONPATH=modules " + " ".join(shlex.quote(p) for p in parts)


def _story_media_args(slot: dict) -> list:
    """Аргументы медиа для команды сторис. Историй у слота бывает ТРИ вида, и
    одной строкой они не описываются:

      * серия кадров («карусель в сторис») — `--media` со списком файлов ПО
        ПОРЯДКУ. Порядок здесь и есть содержание: перепутать кадры значит
        рассыпать рассказ;
      * готовый вертикальный ролик («рилс в сторис») — путь лежит в медиа
        целиком и к каталогу очереди НЕ приклеивается: ролик не пересобирался
        и остался там, где его взяли;
      * одиночный кадр — как было.
    """
    art = _load_artifact(slot) or {}
    media = art.get("media") if not art.get("dry_run") else None
    base = "%s/%s" % (paths.QUEUE_DIR, slot["id"])
    if isinstance(media, dict) and media.get("kind") == "story":
        if media.get("story_kind") == "series" and media.get("files"):
            return ["--media"] + ["%s/%s" % (base, f) for f in media["files"]]
        if media.get("story_kind") == "video" and media.get("file"):
            return ["--media", str(media["file"])]
        if media.get("file"):
            return ["--media", "%s/%s" % (base, media["file"])]
    _flag, target = _artifact_target(slot)
    return ["--media", target]


def build_autopost_command(slot: dict, trial_slot_ids: set = frozenset()) -> str:
    """Собирает ТУ ЖЕ команду autopost.py, что человек ввёл бы руками. Реально
    отправит только штатный --send самого autopost (предохранитель на месте).

    `trial_slot_ids` — из compute_trial_slots(plan): если id слота там есть,
    дописывается --trial <режим клиента>. Флаг существует только для Reels
    (--video) и только когда клиент явно попросил пробные показы — для
    остальных слотов ничего не меняется.

    СТОРИС ИДУТ ДРУГИМ СКРИПТОМ (28.08.2026). autopost.py кладёт пост в ЛЕНТУ,
    а история — другой контейнер: у неё свой срок жизни, свой формат и своя
    выкладка (story_publisher). Раньше сторис-слот молча проваливался в общую
    ветку и собирал команду поста в ленту — то есть инструкция предлагала
    опубликовать историю постом. Предохранитель у story_publisher тот же:
    без --send он ничего не отправляет."""
    if slot.get("format") == "stories":
        return _build_story_command(slot)
    flag, target = _artifact_target(slot)
    plats = ",".join(slot.get("platforms", []) or ["instagram"])
    parts = ["python", "autopost.py", flag, target,
             "--to", plats,
             "--at", slot["publish_at"] + "Z"]
    if flag == "--video" and slot["id"] in trial_slot_ids:
        mode = config.trial_reels_mode()
        if mode != "off":
            parts += ["--trial", mode]
    parts.append("--send")
    return " ".join(shlex.quote(p) for p in parts)


def material_ready(slot: dict):
    """ПЯТЫЙ ЗАМОК: собран ли у выпуска НАСТОЯЩИЙ материал.

    Зачем этот замок вообще есть. Публикация (gate.REAL_PUBLISH_IMPLEMENTED) и
    сборка материалов (executor.CONTENT_PLAN_REAL_GEN) выключены ОТДЕЛЬНО друг
    от друга. Пока сборка идёт вхолостую, в очередь content_plan/queue/ ложится
    не пост, а ОПИСАНИЕ будущего поста («тут была бы карусель на такую-то тему»).
    Если однажды включат публикацию, забыв включить сборку, клиент отправит это
    описание в свой НАСТОЯЩИЙ аккаунт — а убрать пост из чужой ленты уже нельзя.
    Поэтому: нет собранного материала — нет отправки, при любых прочих условиях.

    Возвращает (готов, причина_отказа_или_None). Причина — человеческим языком,
    её печатает планировщик и видит владелец.
    """
    # 1. Сборка материалов выключена целиком — в очереди у ВСЕХ слотов заглушки.
    if not executor.real_gen_enabled():
        return False, ("материалы собираются вхолостую: сборка постов выключена "
                       "(нет %s=1) — в очереди лежат описания будущих постов, а "
                       "не сами посты. Публиковать нечего"
                       % executor.REAL_GEN_ENV)

    # 2. Сборка включена, но именно этому выпуску не хватило исходников
    #    (карусели без фото, reels без говорящего видео или без блоков, сторис
    #    без хука и темы) — слот остаётся заглушкой с пометкой needs_input.
    need = slot.get("needs_input")
    if need:
        return False, ("материал выпуска не собран — не хватает входа: %s" % need)

    # 3. Файла материала в очереди нет или он не читается.
    name = slot.get("artifact")
    if not name:
        return False, ("материал выпуска не найден в очереди content_plan/queue "
                       "(слот не проходил сборку)")
    artifact = _load_artifact(slot)
    if artifact is None:
        return False, ("материал выпуска не читается (%s) — публиковать нечего"
                       % name)

    # 4. Файл есть, но внутри — заглушка dry-run (сборку включили ПОСЛЕ того, как
    #    слот уже собрался вхолостую, и он больше не пересобирался).
    if artifact.get("needs_input"):
        return False, ("материал выпуска собран вхолостую — не хватает входа: %s"
                       % artifact["needs_input"])
    if artifact.get("dry_run") or not artifact.get("media"):
        return False, ("в очереди лежит ЗАГЛУШКА, а не готовый пост (%s собран "
                       "в режиме dry-run) — пересобери слот при включённой "
                       "сборке" % name)
    return True, None


def _locks(plan: dict, slot: dict, now: datetime, armed: bool):
    """Пять замков реальной отправки. Возвращает (ok, причины_отказа).

    1. план утверждён человеком на период;
    2. время выхода наступило (окно-вето истекло);
    3. сам выпуск утверждён (approved);
    4. отправка вооружена (конфиг content_plan_live_publish + запуск с --arm);
    5. у выпуска есть НАСТОЯЩИЙ собранный материал, а не заглушка (material_ready).

    Поверх всех пяти стоит жёсткий стоп REAL_PUBLISH_IMPLEMENTED в run().
    """
    reasons = []
    if not model.is_approved(plan):
        reasons.append("план не утверждён (нет слова человека на период)")
    publish = _parse(slot.get("publish_at"))
    if publish is None or now < publish:
        reasons.append("ещё не время публикации / окно-вето не истекло")
    if slot.get("status") != model.APPROVED:
        reasons.append("выпуск не утверждён (статус: %s — нужен approved)"
                       % model.label(slot.get("status")))
    if not (config.live_publish_enabled() and armed):
        reasons.append("реальная отправка не вооружена (нужны конфиг "
                       "content_plan_live_publish=true И запуск с --arm)")
    ok_material, why_material = material_ready(slot)
    if not ok_material:
        reasons.append(why_material)
    return (not reasons), reasons


def _log(entry: dict) -> None:
    paths.ensure_dirs()
    with paths.publish_log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _last_event(slot: dict):
    """Последнее зафиксированное событие слота (или None)."""
    hist = slot.get("history") or []
    return hist[-1].get("event") if hist else None


def run(plan: dict, now: datetime = None, armed: bool = False) -> dict:
    """Прогон гейта: обновляет окно-вето и обрабатывает созревшие к публикации
    слоты. Меняет plan на месте. Реальной отправки не делает (жёсткий стоп)."""
    now = _now(now)
    mode = plan_mode(plan)
    horizon_days = config.approval_horizon_days(mode)
    report = {"approval_mode": mode, "approval_horizon_days": horizon_days,
              "veto_open": [], "auto_approved": [], "held_manual": [],
              "held_far": [], "awaiting_approval": [], "would_publish": [],
              "held": [], "published": []}

    write_veto_board(plan, now)
    for s in plan.get("slots", []):
        if in_veto_window(s, now):
            report["veto_open"].append(s["id"])

    trial_slot_ids = compute_trial_slots(plan)
    report["trial_reels"] = sorted(trial_slot_ids)

    # Слой пред-разрешения: классифицируем ВСЕ слоты «на утверждении» по режиму
    # (в т.ч. ещё не созревшие — «одобрить месяц вперёд»). Это решение окна-вето/
    # авто, а НЕ публикация: жёсткий стоп ниже держит даже утверждённые слоты.
    #
    # АВТО-ОДОБРЕНИЕ переводит слот generated → approved, но ТОЛЬКО когда клиент
    # реально получил всё окно-вето (now >= publish_at) и не снял слот. Пока окно
    # открыто, слот остаётся «на утверждении» — иначе пред-разрешение съело бы
    # право вето, ради которого окно и существует.
    for slot in list(plan.get("slots", [])):
        if slot.get("status") not in model.PENDING_APPROVAL:
            continue
        decision, why = approval_decision(slot, now, mode, horizon_days)
        if decision == APPROVE_AUTO:
            publish = _parse(slot.get("publish_at"))
            if publish is not None and now >= publish:
                model.set_status(slot, model.APPROVED,
                                 "авто-одобрен: %s; вето не снято" % why)
                slot["approved_by"] = "авто (%s)" % mode
                slot["approved_at"] = model.now_iso()
            report["auto_approved"].append(slot["id"])
        elif decision == APPROVE_MANUAL:
            report["held_manual"].append(slot["id"])
        else:  # APPROVE_FAR
            report["held_far"].append(slot["id"])

    # Созревшие, но НЕ утверждённые выпуски — отдельно в отчёт: это самая частая
    # причина «почему ничего не вышло» в режиме daily.
    for slot in plan.get("slots", []):
        publish = _parse(slot.get("publish_at"))
        if (slot.get("status") in model.PENDING_APPROVAL + (model.REVISING,)
                and publish is not None and now >= publish):
            report["awaiting_approval"].append(slot["id"])

    for slot in plan.get("slots", []):
        publish = _parse(slot.get("publish_at"))
        if slot.get("status") != model.APPROVED or publish is None or now < publish:
            continue

        _decision, appr_why = approval_decision(slot, now, mode, horizon_days)
        ok, reasons = _locks(plan, slot, now, armed)
        cmd = build_autopost_command(slot, trial_slot_ids)

        if not ok or not REAL_PUBLISH_IMPLEMENTED:
            # Ничего не уходит. Фиксируем намерение и причину удержания — но
            # НЕ дублируем запись при каждом прогоне: под --loop это сотни
            # одинаковых HELD в аудит-логе на один слот. Пишем в publish_log и
            # history только на ПЕРВОМ удержании цикла (последнее событие слота
            # ещё не gate:held); дальше только показываем факт в отчёте и на
            # доске-вето. Смена статуса (reslot и т.п.) добавит новое событие —
            # тогда следующее удержание залогируется как новый цикл.
            why = reasons[:]
            why.append("режим одобрения=%s: %s" % (mode, appr_why))
            if not REAL_PUBLISH_IMPLEMENTED:
                why.append("прототип: REAL_PUBLISH_IMPLEMENTED=False (жёсткий стоп)")
            if _last_event(slot) != "gate:held":
                entry = {"at": model.now_iso(), "slot": slot["id"],
                         "decision": "HELD", "reasons": why, "would_run": cmd}
                _log(entry)
                model.add_history(slot, "gate:held", "; ".join(why))
            report["held"].append({"slot": slot["id"], "reasons": why, "cmd": cmd})
            report["would_publish"].append(slot["id"])
            continue

        # СЮДА прототип не доходит (REAL_PUBLISH_IMPLEMENTED=False). Реальная
        # интеграция вызвала бы autopost --send именно здесь.
        _log({"at": model.now_iso(), "slot": slot["id"],
              "decision": "PUBLISHED", "cmd": cmd})
        model.set_status(slot, model.PUBLISHED, "ушло через autopost --send")
        report["published"].append(slot["id"])

    return report
