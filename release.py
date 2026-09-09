# -*- coding: utf-8 -*-
"""
ВЫПУСК ЦЕЛИКОМ ОДНОЙ КОМАНДОЙ — вместо 5–7 ручных вызовов на выпуск.

ЗАЧЕМ. 06.08.2026 выпуск из 4 единиц занял почти три часа, из них публикация —
44 минуты при ~6 минутах машинного времени. Причина скучная: команды звались
по одной, каждая ждала предыдущую, а два временных отказа (502 от сервиса и
не-JSON от ВК) требовали человека, который заметит и повторит.

ЧТО ДЕЛАЕТ. Читает манифест выпуска (release.json), раскладывает каждую единицу
по площадкам ПО ПРАВИЛАМ AGENTS.md и гонит две очереди ОДНОВРЕМЕННО:

    очередь upload-post :  Instagram + Threads/YouTube  →  Telegram-архив
    очередь ВК          :  личная страница + сообщество

Внутри очереди — последовательно (один аккаунт, не злим сервис), между
очередями — параллельно. Настенное время выпуска становится равно самой долгой
очереди, а не сумме всех ног.

ПРЕДОХРАНИТЕЛЬ НЕ ОСЛАБЛЕН. Без --send не уходит ничего: скрипт печатает
раскладку и выходит. Это ровно тот же гейт, что в autopost.py и vkcarousel.py,
просто теперь он один на весь выпуск, а не по одному на каждую команду.

ЗАПУСК:
    python release.py --plan release.json            # показать раскладку
    python release.py --plan release.json --send     # выпустить
    python release.py --plan release.json --only c44 --send
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# Ссылки достаём из вывода дочерних скриптов — они уже печатают их одинаково.
# ⚠️ Формулировки у скриптов РАЗНЫЕ: autopost и vkcarousel печатают
# «ОПУБЛИКОВАНО:», vkpost — «ПОСТ НА СТЕНЕ:», smmbox_post — «ссылка:». Ловим
# все три, иначе успешная публикация выглядит в отчёте как запись без ссылки.
LINK_RE = re.compile(r"(?:ОПУБЛИКОВАНО|ПОСТ НА СТЕНЕ|ссылка):\s*(\S+)")
SKIP_RE = re.compile(r"ПРОПУЩЕНО:.*?(\S+)$", re.M)


def _smmbox_готов():
    """Подключён ли SmmBox. Без него ВК-нога рилсов идёт старым путём.

    Проверяем НАЛИЧИЕ КЛЮЧА, а не сеть: в сеть ходить на раскладке нельзя, а
    у клиента без оплаченного тарифа ключа просто нет — и это не поломка.
    """
    try:
        sys.path.insert(0, os.path.join(HERE, "modules"))
        from publishing import config_reader
        return bool(config_reader.value("SMMBOX_API_KEY", start=HERE))
    except Exception:                                        # noqa: BLE001
        return False


def _run(cmd, label):
    """Один вызов дочернего скрипта. Возвращает (label, код, ссылки, хвост лога).

    ⚠️ PYTHONIOENCODING обязателен: без него дочерний процесс пишет в cp1251 и
    падает на первом же русском тире в собственном логе.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t0 = time.time()
    p = subprocess.run([PY] + cmd, cwd=HERE, env=env, text=True,
                       encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout or ""
    links = LINK_RE.findall(out)
    skipped = SKIP_RE.findall(out)
    took = time.time() - t0
    # Код 3 у autopost.py означает «исход не подтверждён» — это не «ОК» и не
    # «СБОЙ», и путать их нельзя: первое требует посмотреть ленту, второе —
    # повторить ногу.
    mark = {0: "OK ", 3: "?? "}.get(p.returncode, "СБОЙ")
    print(f"[{mark}] {label} — {took:.0f} сек" +
          (f" — {', '.join(links)}" if links else ""))
    if p.returncode != 0:
        print("       " + "\n       ".join(out.strip().splitlines()[-6:]))
    return {"нога": label, "код": p.returncode, "ссылки": links,
            "пропущено": skipped, "сек": round(took), "лог": out}


def legs_for(unit, send):
    """Разложить единицу по площадкам согласно AGENTS.md.

    Возвращает два списка: ноги очереди upload-post и ноги очереди ВК.
    """
    name = unit["имя"]
    cap = unit["подпись"]
    short = unit.get("подпись_короткая")
    tail = ["--send"] if send else []
    up, vk = [], []
    # Какие ноги вообще нужны. Полю «ноги» есть цена: если часть выпуска уже
    # ушла (сбой на середине, ручная публикация), повторная нога сделает ДУБЛЬ.
    # Умолчание — все три.
    want = set(unit.get("ноги") or ("основная", "телеграм", "вк"))

    if unit["тип"] == "карусель":
        d = unit["папка"]
        # Instagram и Threads — ОДНОЙ заливкой: слайды уходят один раз, тексты
        # разные (у Threads свой потолок 500 символов). Раньше это были два
        # вызова с полной перезаливкой одних и тех же 8 PNG.
        first = ["autopost.py", "--dir", d, "--caption-file", cap,
                 "--to", "instagram,threads"]
        if short:
            first += ["--threads-caption-file", short]
        if "основная" in want:
            up.append((f"{name}: Instagram+Threads", first + tail))
        if "телеграм" in want:
            up.append((f"{name}: Telegram-архив",
                       ["autopost.py", "--dir", d, "--to", "telegram",
                        "--caption-file", short or cap] + tail))
        if "вк" in want:
            # ПОЛНАЯ подпись, не короткая: потолок ВК огромный, а короткая
            # версия писалась под Threads (500 символов). До 25.08.2026 здесь
            # стояло `short or cap`, и в ВК уезжал огрызок — у r67 233 символа
            # при полной 1526.
            # КАРУСЕЛИ В ВК ИДУТ ЧЕРЕЗ SmmBox. Замер живыми постами 28.08.2026:
            # свой путь (vkcarousel.py, ключ по номеру приложения Kate Mobile)
            # кладёт несколько фото ОДНОЙ записью, но ВК показывает их СЕТКОЙ —
            # плитка 3×3, листать нечего (wall-105689605_19652). Листаемую
            # карусель включает только `properties.posting_mode=carousel`, а его
            # умеет один SmmBox (wall-105689605_19653 — листается).
            # Решение владельца 28.08.2026: уходим с Kate полностью.
            if _smmbox_готов():
                for тип, куда in (("user", "личная страница"),
                                  ("group", "сообщество")):
                    vk.append((f"{name}: ВК {куда} (SmmBox)",
                               ["smmbox_post.py", "--dir", d,
                                "--text-file", cap, "--social", "vk",
                                "--type", тип] + tail))
            else:
                # НЕ МОЛЧА. Без SmmBox ВК-ноги нет вовсе — клиент должен узнать
                # об этом здесь, а не обнаружить пустую стену через неделю.
                print(f"  ⚠ {name}: ВК пропущен — не подключён SmmBox. "
                      f"Карусели во ВКонтакте идут только через него "
                      f"(скажите «подключи сммбокс»).")
    else:
        v = unit["файл"]
        first = ["autopost.py", "--video", v, "--caption-file", cap,
                 "--to", "instagram,youtube"]
        if unit.get("заголовок"):
            first += ["--title-file", unit["заголовок"]]
        if unit.get("первый_комментарий"):
            first += ["--first-comment", unit["первый_комментарий"]]
        if "основная" in want:
            up.append((f"{name}: Instagram+YouTube", first + tail))
        if "телеграм" in want:
            up.append((f"{name}: Telegram-архив",
                       ["autopost.py", "--video", v, "--to", "telegram",
                        "--caption-file", short or cap] + tail))
        if "вк" in want:
            # РИЛСЫ В ВК ИДУТ ЧЕРЕЗ SmmBox, а не через vkpost.py. Замер
            # 25.08.2026: за всю историю vkpost.py не доставил в ВК НИ ОДНОГО
            # рилса (журнал — только карусели), потому что прав `wall` нашему
            # приложению ВК не выдаёт. У SmmBox одобренный `wall` есть.
            # `--vk-auto` — единственный режим, где выходит И клип, И запись на
            # стене; описание клипа уезжает отдельным полем видео.
            # Без подключённого SmmBox остаёмся на старом пути: ломать тем, у
            # кого его нет, нельзя.
            if _smmbox_готов():
                for тип, куда in (("user", "личная страница"),
                                  ("group", "сообщество")):
                    vk.append((f"{name}: ВК {куда} (SmmBox)",
                               ["smmbox_post.py", "--video", v,
                                "--text-file", cap, "--social", "vk",
                                "--type", тип, "--vk-auto"] + tail))
            else:
                vk.append((f"{name}: ВК (страница+сообщество)",
                           ["vkpost.py", "--video", v,
                            "--caption-file", cap] + tail))
    return up, vk


def _published_materials():
    """Что уже уходило — по журналам публикации. Читаем оба: сервисный журнал
    autopost и журнал ВК. Битые строки пропускаем, журнал одного сбоя не роняет.
    """
    seen = set()
    for path, key in ((os.path.join(HERE, ".state", "published_ids.jsonl"), "material"),
                      (os.path.join(HERE, ".state", "vk_published.jsonl"), "material")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                val = rec.get(key)
                if val:
                    seen.add(_material_key(val))
    return seen


def _material_key(value):
    """Привести запись журнала к короткому имени единицы.

    В журнале лежит то, чем material был при отправке: у карусели «out_c44», у
    ролика — путь «out_reels/r53_a_tak_mozhno_bylo.mp4». Приводим оба к «c44» и
    «r53», иначе уже опубликованное не опознаётся и уходит по второму разу.
    """
    name = os.path.basename(str(value).replace("\\", "/").rstrip("/"))
    name = os.path.splitext(name)[0]
    if name.startswith("out_"):
        return name[4:]
    return name.split("_")[0]


def _fresh(path, hours):
    """Собрано недавно? Журналы публикаций ведутся не с начала времён (в этом
    проекте — с 01.08.2026), поэтому «нет записи в журнале» НЕ равно «не
    публиковалось». Без этой проверки автоподбор потащил бы в ленту весь архив
    — поймано на живом прогоне: 19 старых материалов разом.
    """
    try:
        return (time.time() - os.path.getmtime(path)) <= hours * 3600
    except OSError:
        return False


def autoplan(hours=36):
    """Собрать манифест САМ: найти готовое и ещё не опубликованное.

    ЗАЧЕМ. Клиент не должен изучать формат манифеста, чтобы выложить свою
    работу. Он говорит агенту «выложи» — и всё. Материал считается готовым,
    когда у него есть собранные файлы И написанная подпись; уже опубликованное
    отсеиваем по журналам, чтобы повтор не сделал дубль.
    """
    done = _published_materials()
    units = []

    for d in sorted(os.listdir(HERE)):
        full = os.path.join(HERE, d)
        if not (d.startswith("out_c") and os.path.isdir(full)):
            continue
        key = d.replace("out_", "")
        if key in done or not _fresh(full, hours):
            continue
        cap = f"caption_{key}.txt"
        if not os.path.exists(os.path.join(HERE, cap)):
            continue
        if not [p for p in os.listdir(full) if p.lower().endswith(".png")]:
            continue
        u = {"имя": key, "тип": "карусель", "папка": d, "подпись": cap}
        short = f"caption_{key}_threads.txt"
        if os.path.exists(os.path.join(HERE, short)):
            u["подпись_короткая"] = short
        units.append(u)

    reels_dir = os.path.join(HERE, "out_reels")
    if os.path.isdir(reels_dir):
        for f in sorted(os.listdir(reels_dir)):
            if not f.lower().endswith(".mp4"):
                continue
            key = f.split("_")[0]                      # r54_xxx.mp4 -> r54
            if key in done or not _fresh(os.path.join(reels_dir, f), hours):
                continue
            cap = f"caption_{key}.txt"
            if not os.path.exists(os.path.join(HERE, cap)):
                continue
            u = {"имя": key, "тип": "reels",
                 "файл": f"out_reels/{f}", "подпись": cap}
            for name, field in ((f"caption_{key}_short.txt", "подпись_короткая"),
                                (f"title_{key}.txt", "заголовок"),
                                (f"first_comment_{key}.txt", "первый_комментарий")):
                if os.path.exists(os.path.join(HERE, name)):
                    u[field] = name
            units.append(u)

    return {"выпуск": time.strftime("%Y-%m-%d") + " — собрано автоматически",
            "единицы": units}


def main():
    ap = argparse.ArgumentParser(description="Выпуск целиком одной командой")
    ap.add_argument("--auto", action="store_true",
                    help="самому найти готовое и неопубликованное — манифест "
                         "писать не нужно")
    ap.add_argument("--since-hours", type=int, default=36,
                    help="при --auto брать материалы, собранные за последние N "
                         "часов (по умолчанию 36)")
    ap.add_argument("--plan", default="release.json", help="манифест выпуска")
    ap.add_argument("--only", help="выпустить только эти единицы (через запятую)")
    ap.add_argument("--send", action="store_true",
                    help="ДЕЙСТВИТЕЛЬНО опубликовать (без него — только показать)")
    a = ap.parse_args()

    if a.auto:
        plan = autoplan(a.since_hours)
        if not plan["единицы"]:
            sys.exit("Публиковать нечего: всё готовое уже выложено, а у "
                     "остального нет подписи (caption_<имя>.txt).")
    else:
        path = a.plan if os.path.isabs(a.plan) else os.path.join(HERE, a.plan)
        if not os.path.exists(path):
            sys.exit("Нет манифеста выпуска: " + path +
                     "\nЛибо позови с --auto — тогда манифест не нужен вовсе.")
        plan = json.load(open(path, encoding="utf-8"))
    units = plan["единицы"]
    if a.only:
        want = {s.strip() for s in a.only.split(",")}
        units = [u for u in units if u["имя"] in want]
        if not units:
            sys.exit("В манифесте нет единиц с именами: " + a.only)

    # Проверяем ФАЙЛЫ ДО отправки: половина выпуска, упавшая на опечатке в
    # имени подписи, — это ровно тот сценарий, ради которого всё затевалось.
    missing = []
    for u in units:
        targets = [u.get("подпись"), u.get("подпись_короткая"),
                   u.get("заголовок"), u.get("первый_комментарий")]
        if u["тип"] == "карусель":
            targets.append(u.get("папка"))
        else:
            targets.append(u.get("файл"))
        for t in targets:
            if t and not os.path.exists(os.path.join(HERE, t)):
                missing.append(f"{u['имя']}: нет {t}")
    if missing:
        sys.exit("ОСТАНОВКА ДО ОТПРАВКИ:\n  " + "\n  ".join(missing))

    up_all, vk_all = [], []
    for u in units:
        up, vk = legs_for(u, a.send)
        up_all += up
        vk_all += vk

    print(f"ВЫПУСК: {plan.get('выпуск', '—')} · единиц {len(units)}")
    for u in units:
        what = u.get("папка") or u.get("файл")
        print(f"  {u['имя']:5} {u['тип']:9} {what}")
    print(f"\nОчередь upload-post ({len(up_all)}) и очередь ВК ({len(vk_all)}) "
          f"идут ОДНОВРЕМЕННО.")
    for label, cmd in up_all + vk_all:
        print("   ", label, "->", " ".join(cmd))

    if not a.send:
        print("\n[ПРОБНЫЙ ПРОГОН] Ничего не отправлено. Для публикации добавь --send")
        return

    t0 = time.time()
    results = []

    def queue(legs):
        out = []
        for label, cmd in legs:
            out.append(_run(cmd, label))
        return out

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_up = pool.submit(queue, up_all)
        f_vk = pool.submit(queue, vk_all)
        results = f_up.result() + f_vk.result()

    print(f"\n{'=' * 62}\nВЫПУСК ЗА {time.time() - t0:.0f} СЕК\n{'=' * 62}")
    # ⚠️ Об исходе судим ТОЛЬКО по коду возврата ноги — вывод дочернего скрипта
    # для этого не годится (форматы разные, ссылки бывают и у половинчатых
    # исходов). До 22.08.2026 autopost.py уходил с нулём даже когда сервис
    # ответил отказом, и такая нога попадала сюда как «ОК» без ссылки — человек
    # не замечал. Теперь autopost различает: 1 — отказ (поста нет), 3 — исход не
    # подтверждён (пост мог выйти). Разница важна ровно одним: код 3 повторять
    # ВСЛЕПУЮ нельзя, будет дубль.
    failed = [r for r in results if r["код"] not in (0, 3)]
    unknown = [r for r in results if r["код"] == 3]
    for r in results:
        state = {0: "ОК  ", 3: "  ??"}.get(r["код"], "СБОЙ")
        print(f"{state} {r['нога']:38} {r['сек']:>4} сек  "
              f"{' '.join(r['ссылки']) or '—'}")
    if failed:
        print(f"\nНЕ ВЫШЛО: {len(failed)} — перезапусти те же ноги через --only. "
              f"Повтор безопасен: ВК ведёт журнал отправок и не задваивает.")
    if unknown:
        print(f"\nНЕ ПОДТВЕРЖДЕНО (??): {len(unknown)} — сервис не сказал, чем "
              f"кончилось.\nПОСМОТРИ ЛЕНТУ ГЛАЗАМИ, прежде чем повторять: пост "
              f"мог выйти, и повтор сделает дубль.")
    if failed or unknown:
        sys.exit(1)
    print("\nПОСМОТРИ ГЛАЗАМИ: порядок слайдов в свайп-альбоме ВК и не лежит ли "
          "клип на боку. Код считает вложения, но не видит картинку.")


if __name__ == "__main__":
    main()
