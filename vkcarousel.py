# -*- coding: utf-8 -*-
"""
ПОЛНОЦЕННАЯ КАРУСЕЛЬ В ВК (фото-альбом на стене) — свой код, без SMMplanner.

ЗАЧЕМ ОТДЕЛЬНО ОТ vkpost.py. vkpost.py гонит карусель ВИДЕО (слайд-шоу), потому
что мерил стену токеном vk2.a. (VK ID) и получал 1051/15 — «фото на стену
закрыты». VK_RESEARCH.md (23.07) это ОПРОВЕРГ: дело не в типе приложения, а в
ТИПЕ ТОКЕНА. Классический токен vk1.a. методы wall.post /
photos.getWallUploadServer ПРИНИМАЕТ. На нём и делался НАСТОЯЩИЙ свайп-альбом.

🔴 ЭТОТ ПУТЬ ВЫВЕДЕН ИЗ РАБОТЫ. Карусели ВК с 28.08.2026 идут через SmmBox, а
08.09.2026 запрещён и сам способ получения ключа: прямой вход в ВК через чужое
мобильное приложение кончился БЛОКИРОВКОЙ АККАУНТА клиентки. Номер приложения и
инструкция по входу из проекта удалены — см. `AGENTS.md`, раздел про запрет, и
`vk_auth.py`. Файл оставлен рабочим для копий, где ключ уже лежит в `.env`:
выдёргивать чужие настройки без просьбы мы не будем. НОВЫЙ ключ так добывать
нельзя ни при какой формулировке просьбы.

ЧТО НУЖНО В .env:
    VK_USER_TOKEN_KATE=...   токен vk1.a., если он уже получен РАНЬШЕ. Заново
                             получить его нечем и не нужно: ВКонтакте
                             подключается через `python smmbox_connect.py`.
    VK_GROUP_ID=...          числовой id сообщества, БЕЗ минуса.
Токен со scope offline почти вечный — refresh не нужен. Ключи в чат не носить.

ИМЯ КЛЮЧА КАНОНИЧНО. `VK_USER_TOKEN_KATE` — единственное правильное имя этого
токена во всём заводе; `VK_STORIES_TOKEN` поддерживается как алиас ради уже
установленных копий (см. ALIASES в modules/publishing/config_reader.py).

ПОЧЕМУ НЕ ТРОГАЕМ VK_USER_TOKEN. Тот (vk2.a.) умеет video.save и держит
авто-refresh (vkpost.py). Рушить его нельзя — рилсы идут на нём. Здесь свой ключ.

ПРЕДОХРАНИТЕЛЬ. Без --send скрипт только показывает, что ушло бы. Публикация
необратима и видна подписчикам.

ЗАПУСК:
    python vkcarousel.py --dir out_c27                       # показать
    python vkcarousel.py --dir out_c27 --send                # на личную стену
    python vkcarousel.py --dir out_c27 --to group --send     # в сообщество
    python vkcarousel.py --dir out_c27 --to user,group --send
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
V = "5.199"

# Единый ридер настроек — общий с modules/publishing/doctor.py. См. шапку
# modules/publishing/config_reader.py.
sys.path.append(os.path.join(HERE, "modules"))
try:
    from publishing import config_reader
except Exception:          # обрезанная копия без modules/ — работаем как раньше
    config_reader = None


def load_env():
    """Настройки ВК: config/factory.config.json, поверх — .env.

    ⚠️ ДВА ФАЙЛА, А НЕ ОДИН. Мастер установки кладёт секретные ответы в `.env`,
    а несекретные (VK_GROUP_ID — это номер, не секрет) в
    `config/factory.config.json`. Пока читался только `.env`, заполненный
    клиентом номер сообщества сюда не доходил, и карусель останавливалась
    словами «В .env нет VK_GROUP_ID» на настроенной коробке.
    """
    if config_reader is not None:
        return config_reader.merged(start=HERE)
    env = {}
    with open(ENV, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# ОДНО СОЕДИНЕНИЕ НА ПРОГОН. Замер 06.08.2026 к api.vk.com: новое рукопожатие
# на каждый вызов — 2.84 сек, через Session — 0.6 сек. Карусель на два адреса
# делает под сорок вызовов, то есть экономия здесь — минуты, а не секунды.
S = requests.Session()


def call(token, method, **params):
    """Вызов метода ВК с повтором на временном отказе.

    ⚠️ Раньше здесь стоял голый `.json()`. 06.08.2026 ВК отдал на saveWallPhoto
    не-JSON (шлюзовой 504 приходит HTML-страницей) — скрипт упал трейсбеком
    ПОСРЕДИ уже залитых слайдов, и человек повторял выпуск руками. Не-JSON и
    5xx — это «спроси ещё раз», а не провал публикации.
    """
    params.update({"access_token": token, "v": V})
    r = None
    for attempt in range(3):
        try:
            resp = S.post("https://api.vk.com/method/" + method,
                          data=params, timeout=60)
            r = resp.json()
            break
        except (ValueError, requests.RequestException) as e:
            if attempt == 2:
                sys.exit("ВК не ответил осмысленно на %s после 3 попыток: %s"
                         % (method, str(e)[:200]))
            pause = (2, 6)[attempt]
            print("   ВК ответил невнятно на %s (%s) — повтор через %d сек"
                  % (method, type(e).__name__, pause))
            time.sleep(pause)
    if "error" in r:
        e = r["error"]
        sys.exit(vk_error_text(e.get("error_code"), e.get("error_msg")))
    return r["response"]


def vk_error_text(code, raw_msg):
    """Ответ ВК человеческим языком: что случилось и что с этим делать.

    ⚠️ ЭТО НЕ КОСМЕТИКА. Клиент, увидевший голое «VK error 5: User authorization
    failed», не знает ни что такое авторизация, ни почему «вчера работало». Ошибка
    5 у ВК означает ровно одно: ключ больше не принимают. Причин три, и все три
    чинятся одним действием, поэтому его и называем — вместо предложения
    разбираться.

    Сырую формулировку ВК оставляем ПЕРВОЙ строкой: если однажды за тем же кодом
    окажется другая причина, человек увидит их слова, а не только нашу догадку.
    """
    msg = "ВКонтакте отказал. Ответ ВК: ошибка %s, %s" % (code, raw_msg)
    if code == 5:
        msg += (
            "\n\nЧТО ЭТО ЗНАЧИТ: ключ доступа к ВК больше не принимают."
            "\nПОЧЕМУ ТАК БЫВАЕТ (любая из трёх причин, все чинятся одинаково):"
            "\n  * вы сменили пароль ВК или отозвали доступ приложениям;"
            "\n  * включён VPN, который меняет адрес — ВК запоминает, с какого"
            "\n    подключения выдан ключ, и с чужого его не принимает;"
            "\n  * ключ был выдан на час (не сработало право offline) и истёк."
            "\nЧТО СДЕЛАТЬ: получить новый прямой ключ ВК НЕЛЬЗЯ - этот вход"
            "\nзапрещен с 08.09.2026, после него ВК блокирует аккаунт целиком,"
            "\nа снять блокировку может только сам владелец аккаунта."
            "\nПубликация в ВК идет через SmmBox: python smmbox_connect.py"
            "\nОпубликованное раньше никуда не делось, повтор ничего не задвоит:"
            "\nжурнал выпусков помнит, что уже уходило."
        )
    elif code in (15, 1051):
        msg += (
            "\n\nЧТО ЭТО ЗНАЧИТ: ВК не пускает этот ключ к фотографиям на стене."
            "\nПОЧЕМУ: ключ не того формата - нужен vk1.a., а не vk2.a. (VK ID)."
            "\nВыдать новый vk1.a. завод больше не может: прямой вход в ВК"
            "\nзапрещен с 08.09.2026, после него ВК блокирует аккаунт."
            "\nЧТО СДЕЛАТЬ: публикуйте через SmmBox - python smmbox_connect.py"
            "\nПодробности замеров: VK_RESEARCH.md."
        )
    elif code == 214:
        msg += ("\n\nЧТО ЭТО ЗНАЧИТ: ВК запретил запись на эту стену настройками"
                "\nсамой страницы или сообщества. Проверьте, кому разрешено"
                "\nпубликовать записи, и повторите.")
    return msg


STATE_DIR = os.path.join(HERE, ".state")
VK_LOG = os.path.join(STATE_DIR, "vk_published.jsonl")


def _vk_log():
    """Журнал уже отправленных записей. Гитигнорится: это рантайм-состояние
    конкретного аккаунта, а не код."""
    os.makedirs(STATE_DIR, exist_ok=True)
    gi = os.path.join(STATE_DIR, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("*\n")
    return VK_LOG


def already_posted(dirname, target):
    """Ссылка на запись, если этот материал уже уходил на ЭТОТ адрес.

    Зачем: 06.08.2026 заливка упала на середине (не-JSON от ВК), повтор прошёл —
    и осталось неизвестным, не появилась ли на личной странице вторая копия.
    Журнал делает повтор после сбоя безопасным по построению.
    """
    key = os.path.basename(dirname.rstrip("/\\"))
    if not os.path.exists(VK_LOG):
        return None
    with open(VK_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("material") == key and rec.get("target") == target:
                return rec.get("link")
    return None


def mark_posted(dirname, target, link, publish_at=None):
    """Запомнить отправку. `publish_at` (unix-время) значит, что запись легла в
    «Отложенные» ВК и на стене её ещё НЕТ — суточный отчёт обязан считать такие
    отдельно от вышедших, иначе отложенная неделя выглядит как «уже опубликовано»."""
    key = os.path.basename(dirname.rstrip("/\\"))
    rec = {"material": key, "target": target, "link": link,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if publish_at:
        rec["scheduled_for"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                             time.localtime(int(publish_at)))
    with open(_vk_log(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def slides_in(dirname):
    src = dirname if os.path.isabs(dirname) else os.path.join(HERE, dirname)
    if not os.path.isdir(src):
        sys.exit("Нет папки " + src)
    imgs = sorted(p for p in glob.glob(os.path.join(src, "*"))
                  if os.path.splitext(p)[1].lower() in (".png", ".jpg", ".jpeg"))
    if not imgs:
        sys.exit("В папке нет .png/.jpg: " + src)
    if len(imgs) > 10:
        sys.exit("В карусели ВК максимум 10 фото, найдено %d" % len(imgs))
    return imgs


def caption_for(dirname, explicit):
    """Текст записи. --caption-file, иначе vk_post_<имя>.txt, иначе caption_<имя>.txt."""
    if explicit:
        path = explicit if os.path.isabs(explicit) else os.path.join(HERE, explicit)
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    base = os.path.basename(dirname.rstrip("/\\"))          # out_c27
    key = base.replace("out_", "")                          # c27
    for name in ("vk_post_%s.txt" % key, "caption_%s.txt" % key):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    sys.exit("Нет текста записи. Дай --caption-file или создай vk_post_%s.txt "
             "/ caption_%s.txt" % (key, key))


def upload_photo(token, fresh_url, path):
    """Один слайд на сервер стены -> (server, photo, hash) -> saveWallPhoto.

    ВК при частых загрузках подряд отвечает 200, но с пустым photo:'' (поймано
    на 3-м слайде 29.07 — не про файл и не про переиспользование сервера, а про
    ТЕМП). Поэтому пауза между попытками + до 4 повторов, а не падение сразу.

    ⚠️ `fresh_url` — ФУНКЦИЯ, а не строка, и это цена живого сбоя 05.08.2026:
    при публикации в СООБЩЕСТВО ВК отдавал 504 ровно на 3-м слайде, и все пять
    повторов долбились в один и тот же протухший upload_url — то есть повторы
    были обречены, а выпуск падал на середине. Теперь каждая попытка берёт
    СВЕЖИЙ адрес, ровно как это уже делалось между слайдами.
    """
    last = None
    for attempt in range(5):
        try:
            with open(path, "rb") as fh:
                r = requests.post(fresh_url(),
                                  files={"photo": (os.path.basename(path), fh,
                                                   "image/png")},
                                  timeout=180)
            if r.status_code >= 500:          # 502/504 — сервер ВК моргнул
                last = "HTTP %s" % r.status_code
                time.sleep(3.0 * (attempt + 1))
                continue
            r.raise_for_status()
            up = r.json()
            if up.get("photo") and up.get("photo") != "[]":
                return up
            last = up                          # 200, но photo:'' — частим, ждём
        except requests.RequestException as ex:
            last = str(ex)
        time.sleep(3.0 * (attempt + 1))
    sys.exit("Сервер ВК не принял фото %s после повторов: %s"
             % (os.path.basename(path), last))


def post_album(token, imgs, message, group_id=None, publish_at=None):
    """Залить все слайды и опубликовать ОДНОЙ записью-альбомом на стене.

    ВАЖНО: upload-сервер стены берём СВЕЖИЙ на КАЖДЫЙ слайд. Один и тот же
    upload_url переиспользовать нельзя — со второго-третьего раза ВК возвращает
    пустой photo:'' (поймано живьём 29.07 на 3-м слайде). Лишний вызов
    getWallUploadServer дешевле, чем сорванная публикация.
    """
    gid_param = {"group_id": str(group_id).lstrip("-")} if group_id else {}
    fresh_url = lambda: call(token, "photos.getWallUploadServer",
                             **gid_param)["upload_url"]
    attachments = []
    for i, img in enumerate(imgs, 1):
        if i > 1:
            # 1 сек хватало личной странице, сообществу — нет: 05.08.2026 ВК
            # ронял 504 на 3-м слайде обеих каруселей. Полторы секунды дешевле
            # ещё одного разбирательства «почему в группе пусто».
            time.sleep(1.5)
        up = upload_photo(token, fresh_url, img)
        saved = call(token, "photos.saveWallPhoto",
                     server=up["server"], photo=up["photo"], hash=up["hash"],
                     **gid_param)
        ph = saved[0]
        attachments.append("photo%s_%s" % (ph["owner_id"], ph["id"]))
        print("   слайд %d/%d залит: %s" % (i, len(imgs), attachments[-1]))
    params = {"message": message, "attachments": ",".join(attachments)}
    if group_id:
        params["owner_id"] = "-" + str(group_id).lstrip("-")
        params["from_group"] = "1"
    # Отложенная запись: ВК сам опубликует её в назначенное время, компьютер
    # для этого не нужен. Так неделя контента заливается заранее одним заходом.
    if publish_at:
        params["publish_date"] = int(publish_at)
    res = call(token, "wall.post", **params)
    owner = params.get("owner_id", "")
    link = "https://vk.com/wall%s_%s" % (owner or "", res["post_id"])
    if publish_at:
        # Отложенная запись ещё не на стене: wall.getById вернёт её, но считать
        # вложения бессмысленно до выхода — проверка сработает уже по факту.
        print("   ОТЛОЖЕНО на %s (запись в «Отложенных»)"
              % time.strftime("%d.%m %H:%M", time.localtime(publish_at)))
        return link
    check_post(token, owner, res["post_id"], "photo", len(imgs))
    return link


def check_post(token, owner, post_id, kind, expect):
    """Перечитать опубликованную запись и пересчитать вложения.

    Зачем: `wall.post` возвращает post_id и на пустой записи тоже — «ОК» в логе
    ещё не значит, что до стены доехали все слайды. Считаем сами.

    ⚠️ На ЛИЧНОЙ странице `wall.getById` закрыт (ошибка 1051 «Method is not
    available for this profile type») — там проверка честно говорит, что глазами
    смотрит человек. В СООБЩЕСТВЕ метод работает, и оно проверяется по-настоящему.
    """
    # Для личной страницы owner в wall.post не передавался — восстанавливаем свой
    # id, иначе `posts` собирается как "_12190" и ВК отвечает ошибкой 100, а
    # публикация (уже прошедшая!) падает на проверке.
    if not owner:
        owner = str(call(token, "users.get")[0]["id"])
    try:
        r = call(token, "wall.getById", posts="%s_%s" % (owner, post_id))
    except SystemExit as ex:
        # Молчим ТОЛЬКО про закрытый метод. Протухший токен или смена IP обязаны
        # долетать до человека, иначе проверка врёт про спокойствие.
        if "1051" not in str(ex) and "ошибка 15" not in str(ex):
            raise
        print("   ⚠️ проверить запись через API нельзя (личная страница) — "
              "посмотри глазами")
        return
    items = r["items"] if isinstance(r, dict) else r
    got = [a["type"] for a in items[0].get("attachments", [])]
    if got.count(kind) != expect:
        print("   🔴 ЗАПИСЬ НЕПОЛНАЯ: ожидали %d %s, на стене %d %s"
              % (expect, kind, got.count(kind), got))
        return
    print("   проверено: на стене %d %s — столько же, сколько отправляли"
          % (expect, kind))


def main():
    ap = argparse.ArgumentParser(description="Полноценная карусель (фото-альбом) в ВК")
    ap.add_argument("--dir", required=True, help="папка карусели, напр. out_c27")
    ap.add_argument("--to", default="user,group",
                    help="куда: user, group или user,group "
                         "(ПО УМОЛЧАНИЮ ОБА — см. комментарий в main)")
    ap.add_argument("--caption-file", help="файл с текстом записи")
    ap.add_argument("--force", action="store_true",
                    help="публиковать, даже если журнал говорит, что этот адрес "
                         "уже получил эту карусель (осознанная вторая копия)")
    ap.add_argument("--send", action="store_true",
                    help="ДЕЙСТВИТЕЛЬНО отправить (без него — только показать)")
    ap.add_argument("--at", help="отложенная публикация, МСК: \"2026-08-15 09:15\". "
                                 "Запись ложится в «Отложенные» и выходит сама — "
                                 "компьютер в этот момент не нужен")
    a = ap.parse_args()

    publish_at = None
    if a.at:
        msk = datetime.timezone(datetime.timedelta(hours=3))
        publish_at = datetime.datetime.strptime(
            a.at, "%Y-%m-%d %H:%M").replace(tzinfo=msk).timestamp()

    targets = [t.strip().lower() for t in a.to.split(",") if t.strip()]
    bad = [t for t in targets if t not in ("group", "user")]
    if bad:
        sys.exit("--to принимает user и group, а не: " + ", ".join(bad))

    imgs = slides_in(a.dir)
    cap = caption_for(a.dir, a.caption_file)

    print("=" * 60)
    print("ЧТО     : карусель %s -> ФОТО-АЛЬБОМ на стене (%d слайдов)"
          % (a.dir, len(imgs)))
    print("КУДА    : %s" % ", ".join(
        "сообщество" if t == "group" else "личная страница" for t in targets))
    print("ТЕКСТ   : %d симв." % len(cap))
    print("-" * 60)
    print(cap)
    print("-" * 60)

    if not a.send:
        print("[ПРОБНЫЙ ПРОГОН] Ничего не отправлено. Для публикации добавь --send")
        return

    # 🔴 ЗАПРЕТ ОТ 08.09.2026, СИЛЬНЕЕ ПРОВЕРКИ КЛЮЧА. Раньше здесь брался ключ
    # из .env и, если он был, карусель уходила. Теперь отказ стоит ДО чтения
    # ключа: запрещён не способ его получить, а любая работа этим ключом.
    # Ключ у клиента в .env остаётся — выдёргивать чужие настройки не наше дело.
    print(
        "ВКОНТАКТЕ: ПРЯМАЯ ПУБЛИКАЦИЯ ЗАПРЕЩЕНА С 08.09.2026.\n"
        "Ключ vk1.a. добывался входом через чужое мобильное приложение, и после\n"
        "такого входа ВКонтакте блокирует аккаунт целиком - снять блокировку может\n"
        "только сам владелец аккаунта. Поэтому мы им больше не публикуем: ни новым\n"
        "ключом, ни тем, что уже лежит в настройках. Удалять его из .env не нужно,\n"
        "он просто не используется.\n"
        "ЧТО РАБОТАЕТ: публикация в ВК идет через SmmBox - python smmbox_connect.py\n")
    sys.exit(2)

    # ⚠️ Умолчание `--to` — ОБА адреса, и это не вкусовщина. 05.08.2026 весь
    # выпуск (c41, c42, r51, r52) уехал только на личную страницу: команда
    # звалась без `--to`, а умолчанием был `user`. Сообщество осталось пустым,
    # и заметила это Валерия, а не код — молчаливая недоставка ничем не пахнет.
    # Публикуем в оба адреса; ограничивать — осознанно, флагом.
    if "group" in targets and not env.get("VK_GROUP_ID"):
        sys.exit("НЕ ЗАДАН НОМЕР СООБЩЕСТВА ВК (VK_GROUP_ID) — в сообщество "
                 "публиковать некуда.\n"
                 "Он спрашивается в мастере установки («Номер сообщества "
                 "ВКонтакте») и лежит\n"
                 "в config/factory.config.json, а не в .env. Либо позовите с "
                 "--to user, если это осознанно.")

    for t in targets:
        gid = env["VK_GROUP_ID"] if t == "group" else None
        print("\n--- %s ---" % ("СООБЩЕСТВО" if t == "group" else "ЛИЧНАЯ СТРАНИЦА"))
        done = already_posted(a.dir, t)
        if done and not a.force:
            print("   ПРОПУЩЕНО: этот адрес уже получил карусель — %s" % done)
            print("   (повтор после сбоя на другом адресе не задваивает запись; "
                  "нужна вторая копия — зови с --force)")
            continue
        link = post_album(token, imgs, cap, group_id=gid, publish_at=publish_at)
        mark_posted(a.dir, t, link, publish_at=publish_at)
        # Отложенной записи на стене ЕЩЁ НЕТ — не называем её опубликованной
        # (post_album выше уже сказал «ОТЛОЖЕНО на …»).
        print("   %s: %s" % ("ССЫЛКА (выйдет по расписанию)" if publish_at
                             else "ОПУБЛИКОВАНО", link))

    print("\nПОСМОТРИ ГЛАЗАМИ: свайп ли это альбом, порядок слайдов, текст под ним.")


if __name__ == "__main__":
    main()
