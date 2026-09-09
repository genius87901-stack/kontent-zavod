# -*- coding: utf-8 -*-
"""
СТОРОЖ БАНКА МЕДИА — проверяет, не появились ли новые фото и видео.

Зачем. Валерия досылает кадры на Google Диск и не обязана об этом сообщать.
Раньше окно узнавало о новой партии случайно: собирали контент на старом банке,
а свежие кадры лежали неиспользованными. Теперь проверка идёт САМА при заходе в
Claude (хук SessionStart) — до того, как собирается первая единица контента.

Что делает:
  --report (по умолчанию)  сравнить папки-источники с реестром и сказать, что
                           нового. Ничего не меняет;
  --import                 завести найденное в банк: фото через import_photos.py,
                           видео копированием в video/ + video_sheet.py;
  --seed                   принять текущее содержимое источников за «уже
                           известное», ничего не заводя.

ПОЧЕМУ РЕЕСТР, А НЕ СВЕРКА ПО СОДЕРЖИМОМУ. import_photos.py сверяет дубли по
sha1 ГОТОВОГО jpg в photos/, а на Диске лежат исходные HEIC — суммы не совпадают
никогда. Запуск на всей папке 09.08 завёл бы весь архив по второму разу (банк
233 → 567, дубль на каждом листе). Поэтому «новизна» определяется по реестру
исходников `.state/media_seen.json`, а не по содержимому банка.

ПЕРВЫЙ ЗАПУСК НИЧЕГО НЕ ЗАВОДИТ. Пустой реестр означает «сторож только
поставили», а не «весь архив новый»: текущее содержимое папок принимается за
известное, об этом печатается строка. Иначе первая же проверка предложила бы
завести 334 файла, которые давно в банке.

ЗАПУСК:
    python media_watch.py                 # проверить и доложить
    python media_watch.py --import        # завести новое в банк
    python media_watch.py --json          # то же, но машинно-читаемо
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".state", "media_seen.json")
CONFIG = os.path.join(HERE, "media_watch.json")
VIDEO_BANK = os.path.join(HERE, "video")

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".heic")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi")

#: Куда смотреть по умолчанию. У учеников пути свои — они задаются в
#: media_watch.json рядом со скриптом (см. ученикам/26_НОВЫЕ_МЕДИА_СТОРОЖ.md),
#: и тогда этот список не используется.
DEFAULT_SOURCES = [
    {"kind": "photo", "path": r"G:\Мой диск\karusel_media\Valeriya photos"},
    {"kind": "video", "path": r"G:\Мой диск\karusel_media\Valeriya video"},
    {"kind": "photo", "path": os.path.join(HERE, "фото для слайдов")},
]

#: Диск иногда висит (см. правило «завис Drive > 5 мин — перезапускать»).
#: Сторож не должен держать заход в Claude: не достучались за это время —
#: докладываем и выходим спокойно.
SCAN_TIMEOUT_SEC = 20


def load_sources():
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        src = data.get("sources")
        if src:
            return src
    return DEFAULT_SOURCES


def load_state():
    if not os.path.exists(STATE):
        return {}
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Битый реестр — не повод падать в лицо человеку. Пересоберётся сам.
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def key_of(path, st):
    """Ключ файла: имя + размер. Без sha1 — читать сотни HEIC с сетевого Диска
    на каждом заходе слишком долго, а имя с камеры уникально и размер устойчив
    (Drive при синхронизации меняет mtime, поэтому времени в ключе нет)."""
    return "%s|%d" % (os.path.basename(path).lower(), st.st_size)


def scan(folder, exts, deadline):
    """Плоский список файлов нужных расширений. Возвращает (files, ok).
    ok=False — папка недоступна или скан не уложился в срок."""
    if not os.path.isdir(folder):
        return [], False
    found = []
    try:
        for name in os.listdir(folder):
            if time.time() > deadline:
                return found, False
            if not name.lower().endswith(exts):
                continue
            p = os.path.join(folder, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            found.append((p, key_of(p, st)))
    except OSError:
        return [], False
    return found, True


def check(sources, state):
    """Что нового по каждому источнику. Реестр не трогает."""
    deadline = time.time() + SCAN_TIMEOUT_SEC
    result = {"new": {"photo": [], "video": []}, "unreachable": [], "seeded": False}
    seen = state.setdefault("seen", {})

    for src in sources:
        kind = src.get("kind", "photo")
        path = src.get("path", "")
        exts = PHOTO_EXT if kind == "photo" else VIDEO_EXT
        files, ok = scan(path, exts, deadline)
        if not ok:
            result["unreachable"].append(path)
            continue
        for full, k in files:
            if k not in seen:
                result["new"][kind].append({"path": full, "key": k, "kind": kind})
    return result


def seed(sources, state, result):
    """Принять всё найденное за известное — без заведения в банк."""
    seen = state.setdefault("seen", {})
    for kind in ("photo", "video"):
        for item in result["new"][kind]:
            seen[item["key"]] = {"kind": kind, "seen_at": time.strftime("%Y-%m-%d"),
                                 "imported": False, "note": "принято при заведении сторожа"}
    state["seeded_at"] = time.strftime("%Y-%m-%d %H:%M")
    save_state(state)


def _copy_with_retry(src, dst, tries=5, delay=2.0):
    """copy2, переживающий свежескачанный Диском файл.

    16.08.2026: сразу после того, как GoogleDriveFS реально стягивает файл из
    облака (особенно после перезапуска зависшего процесса — см.
    feedback_restart_gdrive_on_hang), он на секунду-другую занят самим Диском
    (или подхватившим его антивирусом) — `shutil.copy2` падает
    `PermissionError: занят другим процессом`, хотя файл уже физически на
    месте. Повтор с паузой чинит это без участия человека; если файл и правда
    недоступен (не транзиент) — падаем честно после всех попыток."""
    last = None
    for attempt in range(tries):
        try:
            shutil.copy2(src, dst)
            return
        except (PermissionError, OSError) as e:
            last = e
            if attempt + 1 < tries:
                time.sleep(delay)
    raise last


def import_photos(items):
    """Новые фото — во временную папку и одним вызовом import_photos.py.

    Именно так, а не `--src` на всю папку Диска: скрипт импорта сверяет дубли по
    sha1 готового jpg, для HEIC-исходников эта сверка не срабатывает никогда.

    Возвращает СПИСОК элементов из `items`, которые реально скопировались —
    только их можно потом честно пометить «увидено» (см. `mark_imported`).
    16.08.2026: раньше это было bool на всю пачку разом — при падении ОДНОГО
    файла помечались (или не помечались) ВСЕ, включая успешные."""
    tmp = os.path.join(HERE, ".state", "import_batch")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    copied, failed = [], []
    for it in items:
        try:
            _copy_with_retry(it["path"], os.path.join(tmp, os.path.basename(it["path"])))
            copied.append(it)
        except (PermissionError, OSError) as e:
            # Один недоступный файл (Диск не досинхронизировал, файл реально
            # битый и т.п.) не должен блокировать весь остальной пакет —
            # раньше именно так и происходило: одно исключение здесь роняло
            # ВЕСЬ импорт, включая уже успешно скопированные файлы.
            failed.append((os.path.basename(it["path"]), str(e)))
    if failed:
        print(f"⚠ Пропущено {len(failed)} файлов (не читаются даже после повторов, "
              "заведём в другой раз):")
        for name, err in failed:
            print(f"    {name} — {err}")
    if not copied:
        print("Копировать было нечего — все файлы пакета пропущены.")
        shutil.rmtree(tmp, ignore_errors=True)
        return [], False
    r = subprocess.run([sys.executable, os.path.join(HERE, "import_photos.py"),
                        "--src", tmp], cwd=HERE)
    shutil.rmtree(tmp, ignore_errors=True)
    # Пересборка контрольного листа/индекса — ОДНА команда на всю пачку: если
    # она упала, доверять нечему, честнее не помечать никого «увиденным», чем
    # соврать, что часть точно долетела.
    if r.returncode != 0:
        print("⚠ import_photos.py завершился с ошибкой — в банк не заведено НИЧЕГО.")
        return [], False
    return copied, True


def import_video(items):
    """Видео просто ложатся в video/ — банк роликов это папка. После копирования
    пересобирается контрольный лист ALL_VIDEO.jpg (vidx.json нумерует ролики).

    Возвращает список реально скопированных элементов (см. `import_photos` —
    та же логика: 16.08.2026 весь список помечался «увиденным» одним булевым
    возвратом `video_sheet.py`, и любой сбой пересборки листа — даже когда все
    файлы физически легли в video/ — заставлял КАЖДЫЙ следующий запуск снова
    считать их «новыми» навсегда."""
    os.makedirs(VIDEO_BANK, exist_ok=True)
    copied, failed = [], []
    for it in items:
        dst = os.path.join(VIDEO_BANK, os.path.basename(it["path"]))
        try:
            if not os.path.exists(dst):
                _copy_with_retry(it["path"], dst)
            copied.append(it)
        except (PermissionError, OSError) as e:
            failed.append((os.path.basename(it["path"]), str(e)))
    if failed:
        print(f"⚠ Пропущено {len(failed)} видео (не читаются даже после повторов, "
              "заведём в другой раз):")
        for name, err in failed:
            print(f"    {name} — {err}")
    if not copied:
        return [], False
    r = subprocess.run([sys.executable, os.path.join(HERE, "video_sheet.py")], cwd=HERE)
    if r.returncode != 0:
        print("⚠ Видео скопированы в video/, но контрольный лист ALL_VIDEO.jpg "
              "не пересобрался — пересобери отдельно: python video_sheet.py")
        # Файлы физически на месте — это НЕ повод считать их «новыми» вечно,
        # в отличие от фото (где без успешного import_photos.py файлов в
        # банке просто нет вообще). Но и «готово» это не значит: ok=False.
        return copied, False
    return copied, True


def mark_imported(state, items):
    seen = state.setdefault("seen", {})
    for it in items:
        seen[it["key"]] = {"kind": it["kind"], "seen_at": time.strftime("%Y-%m-%d"),
                           "imported": True, "source": it["path"]}
    save_state(state)


def report(result, quiet=False):
    """Человеческий отчёт. При --quiet молчит, когда нового нет: сторож висит на
    каждом заходе в Claude и не должен шуметь без повода."""
    photo = result["new"]["photo"]
    video = result["new"]["video"]
    lines = []

    if result["seeded"]:
        lines.append("Сторож банка медиа заведён. Принято за известное: "
                     "%d фото, %d видео. Заводить их не нужно — они уже в банке."
                     % (len(photo), len(video)))
        lines.append("Дальше буду сообщать только о новых файлах.")
        print("\n".join(lines))
        return

    if photo or video:
        lines.append("НОВОЕ МЕДИА НА ДИСКЕ — до сборки контента завести в банк:")
        if photo:
            lines.append("  фото: %d" % len(photo))
            for it in photo[:10]:
                lines.append("    %s" % os.path.basename(it["path"]))
            if len(photo) > 10:
                lines.append("    … и ещё %d" % (len(photo) - 10))
        if video:
            lines.append("  видео: %d" % len(video))
            for it in video[:10]:
                lines.append("    %s" % os.path.basename(it["path"]))
            if len(video) > 10:
                lines.append("    … и ещё %d" % (len(video) - 10))
        lines.append("  завести:  python media_watch.py --import")
    elif not quiet:
        lines.append("Новых фото и видео нет — банк актуален.")

    if result["unreachable"] and not quiet:
        lines.append("Не удалось прочитать: %s" % ", ".join(result["unreachable"]))
        lines.append("(Google Диск может быть не смонтирован или подвис.)")

    if lines:
        print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Проверка новых фото и видео в банке")
    ap.add_argument("--import", dest="do_import", action="store_true",
                    help="завести найденное в банк")
    ap.add_argument("--seed", action="store_true",
                    help="принять текущее содержимое за известное, не заводя")
    ap.add_argument("--quiet", action="store_true",
                    help="молчать, если нового нет (режим хука)")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    a = ap.parse_args()

    # Windows перенаправляет stdout в cp1251, и русский отчёт приезжает в хук
    # кракозябрами — а его читает и человек, и Claude.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    sources = load_sources()
    state = load_state()
    first_run = not state.get("seen")

    result = check(sources, state)

    # Первый запуск: банк уже собран из этих файлов — принимаем их за известные.
    if first_run and not a.do_import:
        seed(sources, state, result)
        result["seeded"] = True
    elif a.seed:
        seed(sources, state, result)
        result["seeded"] = True

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return

    if a.do_import and not result["seeded"]:
        photo, video = result["new"]["photo"], result["new"]["video"]
        if not photo and not video:
            print("Заводить нечего — новых файлов нет.")
            return
        ok = True
        if photo:
            done, done_ok = import_photos(photo)
            ok = ok and done_ok
            if done:
                mark_imported(state, done)
        if video:
            done, done_ok = import_video(video)
            ok = ok and done_ok
            if done:
                mark_imported(state, done)
        if not ok:
            # 05.09.2026: здесь печаталось «Готово. Контрольные листы
            # пересобраны» и возвращался код 0 даже после трассировки в
            # дочернем скрипте (не было PIL — 48 фото не завелись, а отчёт
            # был победный). Провал обязан выглядеть провалом — и для
            # человека, и для того, кто читает код возврата.
            print("⚠ ЗАВЕДЕНО НЕ ВСЁ — смотри сообщения выше. Банк и "
                  "контрольные листы могли остаться прежними.")
            raise SystemExit(1)
        # Про лист говорим осторожно: он пересобирается, только когда есть что
        # заводить (см. import_photos.py, флаг --sheet).
        print("Готово. Смотри ALL_PHOTOS.jpg / ALL_VIDEO.jpg перед выбором кадров.")
        return

    report(result, quiet=a.quiet)


if __name__ == "__main__":
    main()
