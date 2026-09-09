# -*- coding: utf-8 -*-
"""
СУТОЧНЫЙ ОТЧЁТ О ПУБЛИКАЦИЯХ — в Telegram-архив, текстовым сообщением.

ЗАЧЕМ. После автовыпуска никто не подводит итог: что РЕАЛЬНО вышло в ленту,
что ещё ждёт своей даты, где план вот-вот кончится и где обещанное не вышло.
Отчёт закрывает все четыре вопроса одним сообщением раз в сутки.

ГЛАВНОЕ РАЗЛИЧЕНИЕ, РАДИ КОТОРОГО ВСЁ ЗАТЕВАЛОСЬ: «принято сервисом» ≠
«вышло в ленту». Отложенный пост принимается за две недели до выхода, и в
локальном журнале `.state/published_ids.jsonl` стоит дата ПРИЁМА заявки.
Считать по нему «сколько опубликовано» — значит записать в сегодняшний успех
168 постов, которых в лентах ещё нет. Поэтому:

  ВЫШЛО   — только `GET /uploadposts/history` (`upload_timestamp` + `success`):
            это журнал самого сервиса о РЕАЛЬНО ушедших постах. Плюс ВК своим
            журналом (Upload-Post про ВК не знает).
  ОТЛОЖЕНО — наш журнал, записи со `scheduled_for` в будущем.

РАЗРЕЗЫ ОТЧЁТА (просьба владельца 22.08.2026):
  * по каждой площадке отдельно;
  * внутри площадки — карусели и рилсы отдельными строками;
  * пробные Reels (Instagram Trial Reels) — отдельной строкой;
  * отложенные записи ВКонтакте — отдельным блоком (они лежат в «Отложенных»
    ВК, а не у Upload-Post, и до выхода их не видно нигде);
  * докуда расписан контент + предупреждение за 2 дня до конца плана;
  * расхождения «обещали на дату — в ленте нет».

ПРЕДОХРАНИТЕЛЬ: без --send только печатает, в Telegram ничего не уходит.

ЗАПУСК:
    python daily_report.py                  # за вчера (МСК), только показ
    python daily_report.py --send            # то же + отправка в архив
    python daily_report.py --since 2026-08-22 --until 2026-08-23 --send
    python daily_report.py --check-only      # только расхождения и план
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_SELF = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(_SELF, "modules"))
sys.path.insert(0, _SELF)


def _install_root() -> str:
    """Корень УСТАНОВКИ, а не рабочая папка (CONTRACT.md §3).

    Задание Планировщика Windows запускается из своей папки, поэтому «где лежат
    журналы» нельзя выводить из cwd. Канонический ридер коробки —
    install/factory_config.py; его нет (обрезанная копия, дев-запуск) — падаем
    на папку самого файла, как было раньше.
    """
    env = os.environ.get("FACTORY_ROOT")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    try:
        import importlib.util
        cand = os.path.join(_SELF, "install", "factory_config.py")
        if os.path.isfile(cand):
            spec = importlib.util.spec_from_file_location("kz_fc_report", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return str(mod.root(start=_SELF))
    except Exception:                                   # noqa: BLE001
        pass
    return _SELF


HERE = _install_root()

import autopost                       # creds() — единый ридер ключа/профиля
from publishing import config_reader

# Аналитика в коробке клиента может быть НЕ УСТАНОВЛЕНА (это отдельный модуль).
# Отчёт от этого зависеть не должен: журнал публикаций у Upload-Post читается
# одним обычным GET, и запасной путь ниже делает ровно это. Без запасного пути
# у клиента без модуля analytics отчёт падал бы на импорте — то есть молча не
# приходил бы вовсе.
try:
    from analytics import upload_post as up
except Exception:                                       # noqa: BLE001
    up = None

API_HISTORY = "https://api.upload-post.com/api/uploadposts/history"

MSK = dt.timezone(dt.timedelta(hours=3))

# За сколько дней до конца расписанного контента начинать напоминать. Два дня —
# просьба владельца: этого хватает, чтобы придумать и собрать новую партию, но
# напоминание ещё не превращается в фоновый шум.
PLAN_WARN_DAYS = 2

PLATFORM_NAMES = {
    "instagram": "Instagram", "youtube": "YouTube", "threads": "Threads",
    "telegram": "Telegram", "tiktok": "TikTok", "pinterest": "Pinterest",
    "vk": "ВКонтакте", "facebook": "Facebook", "linkedin": "LinkedIn",
    "x": "X", "reddit": "Reddit", "bluesky": "Bluesky",
}

# Тип медиа у сервиса → наше слово. У Upload-Post это "video"/"photo", у нас в
# журнале "reels"/"carousel" — разные словари для одного и того же.
KIND_NAMES = {"reels": "Рилсы", "carousel": "Карусели",
              "video": "Рилсы", "photo": "Карусели"}


def pname(p) -> str:
    return PLATFORM_NAMES.get(str(p), str(p))


def _parse_ts(raw):
    """upload_timestamp приходит то строкой ISO, то {"$date": …} (Mongo extended
    JSON) — видели оба варианта живьём 22.08.2026."""
    if isinstance(raw, dict):
        raw = raw.get("$date")
    if not raw:
        return None
    try:
        t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _parse_when(raw):
    """Время из НАШИХ журналов: ISO с зоной или без. Без зоны — считаем МСК:
    так их пишут vkcarousel.mark_posted и publish_queue (местное время машины)."""
    if not raw:
        return None
    try:
        t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=MSK)
    except ValueError:
        return None


# ── источники ────────────────────────────────────────────────────────────────
def _history_page(key, page, limit=100) -> dict:
    """Одна страница журнала сервиса. Через модуль analytics, если он есть, и
    напрямую, если его в этой коробке не устанавливали."""
    if up is not None:
        return up.upload_history(page=page, limit=limit, key=key)
    import requests
    r = requests.get(API_HISTORY, headers={"Authorization": f"Apikey {key}"},
                     params={"page": page, "limit": limit}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def fetch_history(key, max_pages=15) -> list:
    """Журнал Upload-Post постранично. Единственный источник правды о том, что
    РЕАЛЬНО вышло: наш локальный журнал знает только про приём заявки."""
    items, page = [], 1
    while page <= max_pages:
        try:
            data = _history_page(key, page)
        except Exception as e:                          # noqa: BLE001
            print(f"⚠️ Журнал публикаций Upload-Post недоступен: {e}")
            break
        batch = data.get("history") or []
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def read_jsonl(path: Path) -> list:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def our_log() -> list:
    """Наш журнал заявок, БЕЗ устаревших повторов.

    Одна и та же единица может уходить на площадку дважды: первый заход
    забраковали и переотправили (так было 22.08 со вторым пробным батчем —
    42 поста удалили вручную из кабинета и поставили заново с --trial).
    Обе заявки лежат в журнале, и если считать обе, отчёт покажет вдвое больше
    отложенного, а по удалённым потом поднимет ложную тревогу «время вышло, а
    поста нет». Поэтому на пару «материал + площадка» оставляем САМУЮ ПОЗДНЮЮ
    заявку: переотправка отменяет предыдущую.
    """
    rows = read_jsonl(Path(HERE) / ".state" / "published_ids.jsonl")
    newest = {}
    for r in rows:
        key = (str(r.get("material")), str(r.get("platform")))
        prev = newest.get(key)
        if prev is None or str(r.get("published_at") or "") >= str(prev.get("published_at") or ""):
            newest[key] = r
    return list(newest.values())


def vk_log() -> list:
    return read_jsonl(Path(HERE) / ".state" / "vk_published.jsonl")


def trial_requests(log) -> set:
    """request_id, уходившие пробными (Instagram Trial Reels).

    Связь именно по request_id: Upload-Post в своём журнале про пробный показ
    не сообщает вовсе, а request_id есть с обеих сторон. Сопоставлять по
    заголовку поста нельзя — там текст подписи, а не имя материала.
    """
    return {str(r.get("request_id")) for r in log if r.get("trial")}


# ── разделы отчёта ───────────────────────────────────────────────────────────
def section_published(history, vk, start, end, trials) -> list:
    """Что РЕАЛЬНО вышло в ленту за период — по площадкам и типам."""
    # {площадка: {тип: количество}}, пробные считаем отдельным ключом
    got = defaultdict(lambda: defaultdict(int))
    fails = defaultdict(int)

    for h in history:
        ts = _parse_ts(h.get("upload_timestamp"))
        if ts is None or not (start <= ts.astimezone(MSK) < end):
            continue
        plat = str(h.get("platform") or "?")
        if not h.get("success"):
            fails[plat] += 1
            continue
        kind = KIND_NAMES.get(str(h.get("media_type")), "Прочее")
        got[plat][kind] += 1
        # Пробный показ виден только по нашему журналу, связь — по request_id.
        if str(h.get("request_id")) in trials:
            got[plat]["_trial"] += 1

    for r in vk:
        # На стене — только записи БЕЗ scheduled_for: со scheduled_for они ещё
        # лежат в «Отложенных» ВК.
        if r.get("scheduled_for"):
            continue
        ts = _parse_when(r.get("at"))
        if ts is None or not (start <= ts.astimezone(MSK) < end):
            continue
        mat = str(r.get("material") or "")
        kind = "Карусели" if mat.startswith("out_c") else "Рилсы"
        got["vk"][kind] += 1

    total = sum(n for kinds in got.values()
                for k, n in kinds.items() if not k.startswith("_"))
    lines = [f"✅ ВЫШЛО В ЛЕНТУ: {total}"]
    if not got:
        lines.append("   За период публикаций не было.")
        return lines, fails

    for plat in sorted(got, key=lambda p: -sum(
            n for k, n in got[p].items() if not k.startswith("_"))):
        kinds = got[plat]
        subtotal = sum(n for k, n in kinds.items() if not k.startswith("_"))
        lines.append(f"\n  {pname(plat)} — {subtotal}")
        for kind in ("Карусели", "Рилсы", "Прочее"):
            if kinds.get(kind):
                lines.append(f"     • {kind}: {kinds[kind]}")
        if kinds.get("_trial"):
            lines.append(f"     • из них пробные (Trial Reels): {kinds['_trial']}")
    return lines, fails


def section_scheduled(log, vk, now) -> list:
    """Что ещё НЕ вышло, но уже поставлено: Upload-Post и отдельно «Отложенные» ВК."""
    ahead = defaultdict(lambda: defaultdict(int))
    last_date = {}
    for r in log:
        when = _parse_when(r.get("scheduled_for"))
        if when is None or when <= now:
            continue
        plat = str(r.get("platform") or "?")
        kind = KIND_NAMES.get(str(r.get("media_type")), "Прочее")
        ahead[plat][kind] += 1
        d = when.astimezone(MSK).date()
        last_date[plat] = max(last_date.get(plat, d), d)

    vk_ahead = defaultdict(int)
    for r in vk:
        when = _parse_when(r.get("scheduled_for"))
        if when is None or when <= now:
            continue
        mat = str(r.get("material") or "")
        vk_ahead["Карусели" if mat.startswith("out_c") else "Рилсы"] += 1
        d = when.astimezone(MSK).date()
        last_date["vk"] = max(last_date.get("vk", d), d)

    lines = []
    total = sum(n for k in ahead.values() for n in k.values()) + sum(vk_ahead.values())
    if not total:
        return lines, last_date

    lines.append(f"\n⏳ ЖДЁТ ВЫХОДА (уже поставлено): {total}")
    for plat in sorted(ahead, key=lambda p: -sum(ahead[p].values())):
        sub = sum(ahead[plat].values())
        upto = last_date.get(plat)
        tail = f", до {upto.strftime('%d.%m')}" if upto else ""
        lines.append(f"  • {pname(plat)}: {sub}{tail}")
        for kind in ("Карусели", "Рилсы"):
            if ahead[plat].get(kind):
                lines.append(f"       {kind}: {ahead[plat][kind]}")
    if vk_ahead:
        upto = last_date.get("vk")
        tail = f", до {upto.strftime('%d.%m')}" if upto else ""
        lines.append(f"  • ВКонтакте — в «Отложенных» ВК: "
                     f"{sum(vk_ahead.values())}{tail}")
        for kind in ("Карусели", "Рилсы"):
            if vk_ahead.get(kind):
                lines.append(f"       {kind}: {vk_ahead[kind]}")
    return lines, last_date


def section_plan(last_date, now) -> list:
    """Докуда расписан контент и не пора ли собирать новую партию."""
    lines = ["\n📅 КОНТЕНТ-ПЛАН"]
    if not last_date:
        lines.append("  🔴 Расписанного контента НЕТ вообще — в лентах будет "
                     "окно уже завтра. Нужно собрать и поставить новую партию.")
        return lines
    today = now.astimezone(MSK).date()
    horizon = min(last_date.values())
    for plat in sorted(last_date, key=lambda p: last_date[p]):
        days = (last_date[plat] - today).days
        mark = "🔴" if days <= 0 else ("⚠️" if days <= PLAN_WARN_DAYS else "  ")
        lines.append(f"  {mark} {pname(plat)}: расписано до "
                     f"{last_date[plat].strftime('%d.%m')} (осталось {days} дн.)")
    left = (horizon - today).days
    if left <= 0:
        lines.append("\n  🔴 КОНТЕНТ ЗАКОНЧИЛСЯ. Дальше в лентах окно — "
                     "нужно придумать и собрать новую партию сегодня же.")
    elif left <= PLAN_WARN_DAYS:
        lines.append(f"\n  ⚠️ НАПОМИНАНИЕ: контент кончается через {left} дн. "
                     "Пора придумать темы и собрать новую партию, "
                     "чтобы в лентах не было окна.")
    return lines


def section_mismatch(log, history, vk, now, back_days=7, grace_hours=6) -> list:
    """Расхождения: обещали на дату — в ленте нет.

    Сверяем НАШ журнал (что поставлено на дату) с журналом сервиса (что реально
    вышло), по паре «request_id + площадка» — самая надёжная связь из имеющихся.

    `grace_hours` — не поднимаем тревогу сразу в минуту выхода: сервис забирает
    пост воркером с задержкой, да и время в журнале у части старых записей
    восстановлено по плану, а не по факту отправки. Шесть часов — столько же,
    сколько `--catchup-hours` у очереди по умолчанию.
    """
    now_msk = now.astimezone(MSK)
    since = now_msk - dt.timedelta(days=back_days)
    deadline = now_msk - dt.timedelta(hours=grace_hours)
    # что реально вышло: (request_id, площадка) и просто площадка+заголовок
    done_rids = {(str(h.get("request_id")), str(h.get("platform")))
                 for h in history if h.get("success")}
    fail_rids = {(str(h.get("request_id")), str(h.get("platform"))): h
                 for h in history if not h.get("success")}

    missing, failed = [], []
    for r in log:
        when = _parse_when(r.get("scheduled_for"))
        if when is None:
            continue
        when = when.astimezone(MSK)
        # Только прошедшее (с запасом grace_hours) и только за последнюю неделю:
        # старое уже разобрано, будущее ещё не должно было выйти.
        if not (since <= when <= deadline):
            continue
        rid = str(r.get("request_id"))
        plat = str(r.get("platform"))
        mat = str(r.get("material"))
        key = (rid, plat)
        if key in done_rids:
            continue
        if key in fail_rids:
            err = str(fail_rids[key].get("error_message") or "")[:80]
            failed.append((when, mat, plat, err))
        else:
            missing.append((when, mat, plat))

    lines = []
    if not missing and not failed:
        return lines
    lines.append("\n🔴 РАСХОЖДЕНИЯ ПЛАНА И ФАКТА (за 7 дней)")
    for when, mat, plat, err in sorted(failed)[:15]:
        lines.append(f"  ✖ {when.strftime('%d.%m %H:%M')} {mat} → "
                     f"{pname(plat)}: ОТКАЗ. {err}")
    for when, mat, plat in sorted(missing)[:15]:
        lines.append(f"  ? {when.strftime('%d.%m %H:%M')} {mat} → "
                     f"{pname(plat)}: время вышло, подтверждения нет")
    if len(missing) + len(failed) > 30:
        lines.append(f"  … всего расхождений: {len(missing) + len(failed)}")
    lines.append("  Нужно проверить глазами и при необходимости переотправить.")
    return lines


# ── сборка ───────────────────────────────────────────────────────────────────
def build_report(since_date, until_date, check_only=False) -> str:
    start = dt.datetime.combine(since_date, dt.time.min, MSK)
    end = dt.datetime.combine(until_date, dt.time.min, MSK)
    now = dt.datetime.now(MSK)

    # Ключ Upload-Post берём ОСТОРОЖНО. autopost.creds() при пустых настройках
    # завершает процесс — для публикации это правильно, для отчёта нет: клиент,
    # который выкладывает только во ВКонтакте, каждое утро получал бы молчаливый
    # обрыв задания вместо отчёта по ВК. Нет ключа — просто нет этого источника.
    key = config_reader.value("UPLOAD_POST_API_KEY", start=HERE)
    history = fetch_history(key) if key else []
    log = our_log()
    vk = vk_log()
    trials = trial_requests(log)

    period = (since_date.strftime("%d.%m.%Y")
              if since_date == until_date - dt.timedelta(days=1)
              else f"{since_date.strftime('%d.%m')} — "
                   f"{(until_date - dt.timedelta(days=1)).strftime('%d.%m.%Y')}")

    lines = [f"📊 Отчёт по публикациям за {period}", ""]
    if not key:
        # Молчать об этом нельзя: отчёт по одному ВК выглядел бы как «в
        # Instagram ничего не выходило», хотя мы просто не умеем туда смотреть.
        lines.append("ℹ️ Ключ Upload-Post не заполнен — считаю только ВКонтакте. "
                     "Instagram / YouTube / Threads / Telegram / TikTok / "
                     "Pinterest в этот отчёт не попадают.")
        lines.append("")

    if not check_only:
        pub_lines, fails = section_published(history, vk, start, end, trials)
        lines += pub_lines
        if fails:
            lines.append("\n⚠️ ОТКАЗЫ (сервис принял, публикация не вышла)")
            for plat, n in sorted(fails.items(), key=lambda x: -x[1]):
                lines.append(f"  • {pname(plat)}: {n}")

    sched_lines, last_date = section_scheduled(log, vk, now)
    lines += sched_lines
    lines += section_plan(last_date, now)
    lines += section_mismatch(log, history, vk, now)
    return "\n".join(lines)


def send_to_archive(text: str) -> None:
    """Обычное текстовое сообщение — sendMessage (медиа шлёт tg_send.py).
    Ключ и канал — тот же единый ридер, что у всей публикации."""
    import requests
    token = config_reader.value("TG_BOT_TOKEN")
    chat = config_reader.value("TG_CHAT_ID")
    if not token:
        sys.exit("НЕ ЗАДАН TG_BOT_TOKEN — отчёт послать некуда.")
    if not chat:
        sys.exit("НЕ ЗАДАН TG_CHAT_ID — отчёт послать некуда.")
    # Telegram режет сообщение на 4096 символах и отвечает 400 на длинном —
    # режем сами по строкам, чтобы отчёт не пропал целиком из-за длины.
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > 3900:
            chunks.append(cur)
            cur = ""
        cur += line
    chunks.append(cur)
    for n, chunk in enumerate(chunks, 1):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": chunk}, timeout=30)
        j = r.json()
        if not j.get("ok"):
            sys.exit(f"TELEGRAM ОТКАЗАЛ: {j}")
        print(f"Отправлено в архив, часть {n}/{len(chunks)} "
              f"(message_id={j['result']['message_id']}).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Суточный отчёт о публикациях")
    ap.add_argument("--since", help="начало периода YYYY-MM-DD (умолч. — вчера)")
    ap.add_argument("--until", help="конец периода YYYY-MM-DD, НЕ включая (умолч. — сегодня)")
    ap.add_argument("--check-only", action="store_true",
                    help="только план и расхождения, без сводки за сутки")
    ap.add_argument("--send", action="store_true",
                    help="РЕАЛЬНО отправить в архив (иначе — только показ)")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass

    today = dt.datetime.now(MSK).date()
    since_date = dt.date.fromisoformat(a.since) if a.since else today - dt.timedelta(days=1)
    until_date = dt.date.fromisoformat(a.until) if a.until else today

    text = build_report(since_date, until_date, check_only=a.check_only)
    print(text)

    if not a.send:
        print("\n[ПРОБНЫЙ ПРОГОН] Не отправлено. Для отправки добавь --send")
        return 0

    send_to_archive(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
