# -*- coding: utf-8 -*-
"""
АВТОПОСТИНГ В ВК — ролики (клип-пост на стене). Свой код, без посредника.

🆕 ОБНОВЛЕНО 29.07. С токеном `VK_USER_TOKEN_KATE` (vk1.a., Kate Mobile) `wall.post`
и фото на стену РАБОТАЮТ — таблица ниже про СТАРЫЙ токен vk2.a. (VK ID). Теперь:
  • видео идёт `video.save` (в Клипы) + `wall.post` с видео во вложении → КЛИП
    ПОСТОМ НА СТЕНЕ, и на личной, и в сообществе. Проверено на r37 29.07.
  • КАРУСЕЛИ больше не гоним слайд-шоу — есть `vkcarousel.py` (свайп-альбом фото).
    Путь --dir здесь оставлен как легаси-слайдшоу, но по умолчанию — vkcarousel.py.
Токен Kate со scope offline не протухает; берётся первым (см. main). Всё, что ниже
про 1051/15 «стена закрыта» — верно ТОЛЬКО для vk2.a., исторический контекст.

⚠️ ФАЙЛ ПЕРЕПИСАН 22.07 ПО ЖИВЫМ ЗАМЕРАМ. Прежняя версия (21.07) строилась на
догадках и почти вся оказалась неверной. Что выяснилось на самом деле (vk2.a.):

| метод                       | ответ | вывод                                  |
|-----------------------------|-------|----------------------------------------|
| `video.save` (личная+группа)| OK    | ВИДЕО РАБОТАЕТ — на этом всё и держится|
| `wall.post`                 | 1051  | стена закрыта по ТИПУ приложения       |
| `photos.getWallUploadServer`| 15    | фото на стену запрещены                |
| `shortVideo.create`         | 3     | метода НЕ СУЩЕСТВУЕТ                   |

СЛЕДСТВИЯ, КОТОРЫЕ ЗАДАЮТ ВСЮ КОНСТРУКЦИЮ:

1. **ЗАПИСЕЙ НА СТЕНЕ НЕ БУДЕТ.** Ошибка 1051 — «метод недоступен для данного
   типа профиля»: приложения «Подключение VK ID» к стене не пускают в принципе,
   а standalone, который пускал, ВК убрал. Это не лечится ни правами, ни
   перевыпуском ключа. Поэтому `wall.post` тут не зовётся вовсе.
   Контент всё равно доходит: видеозапись видна в ленте, а текст живёт в её
   описании. Проверено живьём на r5 22.07, Валерия подтвердила глазами.

2. **КАРУСЕЛЬ ИДЁТ ВИДЕО, а не фотографиями.** Фото на стену закрыты (15), так
   что единственный путь — собрать слайды в слайд-шоу 9:16 и залить как видео.
   Скрипт делает это сам, вызывая `build_slideshow.py --vertical`.

3. **КЛИПОВ КАК ОТДЕЛЬНОГО РЕЖИМА НЕТ.** `shortVideo.create` не существует, и он
   не нужен: ВК сам кладёт вертикальное видео в Клипы. Флаг `--clip` убран.

4. **ГРУППОВОЙ ТОКЕН ВЫБРОШЕН.** Замер 21.07: с ним доступны только `groups.*`
   (ошибка 27 на всём остальном). В группу постим ПОЛЬЗОВАТЕЛЬСКИМ токеном с
   `group_id` — так `video.save` и отвечает OK.

⚠️ ТОКЕН ЖИВЁТ ЧАС. `offline` в scope не срабатывает, ВК выдаёт 3600 сек. Если
ловишь ошибку 5 — сначала смотри не на права, а на две вещи: не протух ли токен
и не прыгает ли IP. VPN с ротацией адресов роняет почти все методы ВК, потому
что токен привязан к адресу выдачи (`video.save` — единственный, кто это
переживает). Лечится выбором конкретного сервера в VPN-клиенте. На этом сгорело
полдня 22.07, не повторять.

ЧТО НУЖНО В .env:
    VK_USER_TOKEN_KATE=...  ОСНОВНОЙ ключ (vk1.a.): не протухает, умеет и видео,
                            и карусель, и стену. Получается одной командой,
                            которая в сеть не ходит: `python vk_auth.py --kate`
    VK_GROUP_ID=...         числовой id сообщества, БЕЗ минуса
    VK_USER_TOKEN=...       ЗАПАСНОЙ ключ (vk2.a., VK ID): только видео, живёт
                            час, продлевается лишь при полном комплекте
                            VK_REFRESH_TOKEN + VK_APP_ID + VK_DEVICE_ID
Ключи в чат не носить.

ПРЕДОХРАНИТЕЛЬ. По умолчанию скрипт НИЧЕГО НЕ ОТПРАВЛЯЕТ — только показывает,
что ушло бы. Публикация необратима и видна подписчикам.

ЗАПУСК:
    python vkpost.py --video out_reels/r6_5minut.mp4              # показать
    python vkpost.py --video out_reels/r6_5minut.mp4 --send       # на личную
    python vkpost.py --video out_reels/r6_5minut.mp4 --to group --send
    python vkpost.py --dir out_c9 --to user,group --send          # карусель -> видео
"""
import argparse
import datetime
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
V = "5.199"
ENV = os.path.join(HERE, ".env")
VK_ID_TOKEN = "https://id.vk.com/oauth2/auth"     # обмен/обновление токена VK ID

# Единый ридер настроек — общий с modules/publishing/doctor.py. См. шапку
# modules/publishing/config_reader.py: несекретные ответы клиента (VK_APP_ID,
# VK_GROUP_ID) мастер кладёт НЕ в .env, а в config/factory.config.json.
sys.path.append(os.path.join(HERE, "modules"))
try:
    from publishing import config_reader
except Exception:          # обрезанная копия без modules/ — работаем как раньше
    config_reader = None

# ОДИН ТЕКСТ ОШИБОК ВК НА ОБА СКРИПТА. Объяснение «ключ больше не принимают и
# вот что делать» обязано быть ОДНИМ: два экземпляра одного и того же текста
# расходятся молча, и клиент получает два разных совета на одну беду. Общий
# модуль под это завести нельзя — коробка собирается по явному списку файлов
# (build_box.py), и он не в нашей зоне; поэтому текст живёт в vkcarousel.py, а
# сюда приезжает импортом. Нет vkcarousel рядом — работаем короткой строкой,
# публикация от этого падать не должна.
try:
    from vkcarousel import vk_error_text
except Exception:
    vk_error_text = None


def load_env():
    """Настройки ВК: config/factory.config.json, поверх — .env.

    ⚠️ ДВА ФАЙЛА, А НЕ ОДИН. Мастер установки раскладывает ответы клиента по
    флагу «секрет» из манифеста: VK_USER_TOKEN уходит в `.env`, а VK_APP_ID и
    VK_GROUP_ID — в `config/factory.config.json` (это номера, не секреты).
    Пока здесь читался только `.env`, заполненный клиентом номер сообщества не
    доходил сюда никогда: скрипт останавливался словами «В .env нет
    VK_GROUP_ID» на полностью настроенной коробке, а доктор в это же время
    отвечал «готово».

    `.env` кладётся ПОВЕРХ конфига — save_env() дописывает обновлённый токен
    именно туда, и его версия обязана побеждать.
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


def save_env(key, value, path=ENV):
    """Дописать/заменить строку в .env, не трогая остальные ключи.

    Нужно, чтобы авто-обновление токена (refresh_access) сохраняло свежий
    VK_USER_TOKEN и провёрнутый VK_REFRESH_TOKEN обратно в .env.
    """
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    hit = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            hit = True
            break
    if not hit:
        lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return hit


def refresh_access(env):
    """Обновить токен ВК без входа в браузер (VK-10).

    Токен ВК живёт час; VK ID выдаёт refresh_token, которым access-токен
    продлевается. Для ежедневного завода это и есть «настроил раз — работает
    само»: vk_auth.py запускается ОДИН раз (сохраняет refresh_token + device_id),
    а здесь токен обновляется сам перед каждым постингом.

    VK ID проворачивает refresh_token при каждом использовании — новый
    сохраняем обратно. Возвращает (token, None) или (None, причина).
    """
    import secrets
    rt = env.get("VK_REFRESH_TOKEN")
    app_id = env.get("VK_APP_ID")
    dev = env.get("VK_DEVICE_ID")
    if not (rt and app_id and dev):
        missing = [k for k, val in (("VK_REFRESH_TOKEN", rt), ("VK_APP_ID", app_id),
                                    ("VK_DEVICE_ID", dev)) if not val]
        # ⚠️ ИМЕННО ЭТА ВЕТКА И БЫЛА «ВЧЕРА РАБОТАЛО, СЕГОДНЯ ОШИБКА 5».
        # Запасной ключ vk2.a. живёт час. Продлить его можно только всей связкой
        # из трёх значений; их у клиента нет и взять неоткуда — VK_APP_ID
        # требует регистрации разработчика. Поэтому не сообщаем «нет
        # VK_DEVICE_ID» (это ничего не говорит человеку), а называем следствие и
        # выход: подключиться ключом, который не протухает.
        return None, (
            "продлить нечем — не хватает служебных значений (%s).\n"
            "   Что это значит: ключ ВК действует около часа с момента выдачи, "
            "и продлить\n"
            "   его нечем — публикация вот-вот встанет с ошибкой «5».\n"
            "   Как починить насовсем (~3 минуты, один вход в браузере):\n"
            "       python vk_auth.py --kate\n"
            "   Полученный так ключ не протухает и умеет ещё и карусели."
            % ", ".join(missing))
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": app_id,
        "device_id": dev,
        "state": secrets.token_urlsafe(16),
    }
    try:
        r = requests.post(VK_ID_TOKEN, data=payload, timeout=60)
        d = r.json()
    except ValueError:
        return None, "VK ID ответил не JSON: " + r.text[:300]
    except requests.RequestException as ex:
        return None, "сеть при обновлении токена: %s" % ex
    if "access_token" not in d:
        return None, "refresh не удался: %s" % d
    save_env("VK_USER_TOKEN", d["access_token"])
    if d.get("refresh_token"):
        save_env("VK_REFRESH_TOKEN", d["refresh_token"])
    if d.get("device_id"):
        save_env("VK_DEVICE_ID", d["device_id"])
    return d["access_token"], None


# Одно соединение на прогон (замер 06.08.2026: 2.84 сек против 0.6 сек на вызов).
S = requests.Session()


def call(token, method, **params):
    """Вызов метода ВК. Не-JSON и обрыв связи — временные отказы шлюза, их
    повторяем: падать трейсбеком посреди публикации нельзя (см. vkcarousel.call).
    """
    params.update({"access_token": token, "v": V})
    r = None
    for attempt in range(3):
        try:
            r = S.post("https://api.vk.com/method/" + method,
                       data=params, timeout=60).json()
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
        code = e.get("error_code")
        if vk_error_text is not None:
            sys.exit(vk_error_text(code, e.get("error_msg")))
        msg = "ВК ошибка %s: %s" % (code, e.get("error_msg"))
        if code == 5:
            msg += ("\n   -> ключ ВК больше не принимают (сменился пароль, "
                    "отозван доступ, скачет адрес из-за VPN либо истёк срок)."
                    "\n      Подключите ВК заново: python vk_auth.py --kate")
        elif code in (15, 1051):
            msg += ("\n   -> ВК не пускает этот ключ к стене и фотографиям: он "
                    "формата vk2.a."
                    "\n      Нужен vk1.a. — python vk_auth.py --kate")
        sys.exit(msg)
    return r["response"]


def caption_for(target, explicit):
    """Текст записи. Ищем vk_post_<имя>.txt рядом с проектом.

    Тексты под ВК пишутся ОТДЕЛЬНО от инстаграмных: там снят футер с ником
    Instagram (в ВК он бессмыслен) и другой призыв — решение Валерии 22.07.
    """
    if explicit:
        path = explicit if os.path.isabs(explicit) else os.path.join(HERE, explicit)
        with open(path, encoding="utf-8") as f:
            return f.read().strip()

    base = os.path.splitext(os.path.basename(target.rstrip("/\\")))[0]
    # r6_5minut.mp4 -> vk_post_r6.txt ; out_c9 -> vk_post_c9.txt
    keys = [base, base.split("_")[0], base.replace("out_", "")]
    for k in keys:
        p = os.path.join(HERE, "vk_post_%s.txt" % k)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    sys.exit("Нет текста записи. Создай vk_post_<имя>.txt или дай --caption-file.\n"
             "   Искал: " + ", ".join("vk_post_%s.txt" % k for k in keys))


def slideshow_from(dirname):
    """Карусель -> вертикальное видео. Фото на стену закрыты, это единственный путь."""
    src = dirname if os.path.isabs(dirname) else os.path.join(HERE, dirname)
    if not os.path.isdir(src):
        sys.exit("Нет папки " + src)
    base = os.path.basename(src.rstrip("/\\"))
    out = os.path.join(HERE, "out_slides", "%s_slideshow_9x16.mp4" % base)
    print("   собираю слайд-шоу 9:16 из %s ..." % base)
    r = subprocess.run([sys.executable, "build_slideshow.py", base, "--vertical"],
                       cwd=HERE, capture_output=True)
    if r.returncode != 0 or not os.path.exists(out):
        sys.exit("build_slideshow упал:\n"
                 + r.stdout.decode("utf-8", "replace")[-1500:]
                 + r.stderr.decode("utf-8", "replace")[-1500:])
    return out


def upload_video(token, path, name, description, group_id=None):
    """video.save + отправка файла. Возвращает (owner_id, video_id).

    Вертикальное видео само попадает в Клипы. Для группы даём group_id — тогда
    ролик принадлежит сообществу (иначе остался бы на личной странице)."""
    params = {"name": name[:120], "description": description}
    if group_id:
        params["group_id"] = str(group_id).lstrip("-")
    srv = call(token, "video.save", **params)
    with open(path, "rb") as fh:
        r = requests.post(srv["upload_url"],
                          files={"video_file": (os.path.basename(path), fh,
                                                "video/mp4")}, timeout=900)
    r.raise_for_status()
    done = r.json()
    owner = srv.get("owner_id") or done.get("owner_id")
    vid = srv.get("video_id") or done.get("video_id")
    return owner, vid


def post_video_wall(token, owner, vid, message, group_id=None, publish_at=None):
    """Запись НА СТЕНЕ с видео во вложении. Работает на токене vk1.a. (Kate);
    на старом vk2.a. давал 1051 — потому раньше жили только на Клипах."""
    params = {"message": message, "attachments": "video%s_%s" % (owner, vid)}
    if group_id:
        params["owner_id"] = "-" + str(group_id).lstrip("-")
        params["from_group"] = "1"
    # Отложенная запись — ВК публикует сам в назначенное время (см. vkcarousel).
    if publish_at:
        params["publish_date"] = int(publish_at)
    res = call(token, "wall.post", **params)
    owner_wall = params.get("owner_id", "")
    if publish_at:
        print("   ОТЛОЖЕНО на %s (запись в «Отложенных»)"
              % time.strftime("%d.%m %H:%M", time.localtime(publish_at)))
        return "https://vk.com/wall%s_%s" % (owner_wall, res["post_id"])
    check_post(token, owner_wall, res["post_id"])
    return "https://vk.com/wall%s_%s" % (owner_wall, res["post_id"])


def check_post(token, owner, post_id):
    """Перечитать запись и убедиться, что видео действительно во вложении.

    `wall.post` отдаёт post_id и для записи без вложений — «ОК» в логе не
    доказательство. В СООБЩЕСТВЕ `wall.getById` работает и проверяет по-настоящему;
    на ЛИЧНОЙ странице метод закрыт (1051), там смотрит человек.
    """
    # Для личной страницы owner_id в wall.post не передаётся — восстанавливаем свой
    # id, иначе `posts` собирается как "_12190", ВК отвечает ошибкой 100 и роняет
    # уже прошедшую публикацию на ровном месте.
    if not owner:
        owner = str(call(token, "users.get")[0]["id"])
    try:
        r = call(token, "wall.getById", posts="%s_%s" % (owner, post_id))
    except SystemExit as ex:
        # Молчим ТОЛЬКО про закрытый метод. Любую другую беду (протухший токен,
        # смена IP) глушить нельзя — иначе проверка сама станет тем, от чего
        # защищает: отрапортует спокойствие там, где его нет.
        if "1051" not in str(ex) and "ошибка 15" not in str(ex):
            raise
        print("   ⚠️ проверить запись через API нельзя (личная страница) — "
              "посмотри глазами")
        return
    items = r["items"] if isinstance(r, dict) else r
    got = [a["type"] for a in items[0].get("attachments", [])]
    if "video" not in got:
        print("   🔴 ЗАПИСЬ БЕЗ ВИДЕО: на стене вложения %s" % (got or "нет"))
        return
    print("   проверено: видео на стене на месте")


def main():
    ap = argparse.ArgumentParser(description="Публикация в ВК: ролики и карусели")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dir", help="папка карусели, напр. out_c9 (уйдёт видео)")
    g.add_argument("--video", help="готовый ролик, напр. out_reels/r6_5minut.mp4")
    ap.add_argument("--to", default="user,group",
                    help="куда: user, group или user,group "
                         "(ПО УМОЛЧАНИЮ ОБА — см. комментарий в main)")
    ap.add_argument("--caption-file", help="файл с текстом записи")
    ap.add_argument("--send", action="store_true",
                    help="ДЕЙСТВИТЕЛЬНО отправить (без него — только показать)")
    ap.add_argument("--at", help="отложенная публикация, МСК: \"2026-08-15 09:00\". "
                                 "Запись ложится в «Отложенные» и выходит сама")
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

    target = a.dir or a.video
    cap = caption_for(target, a.caption_file)

    print("=" * 60)
    if a.dir:
        print("ЧТО     : карусель %s -> ВИДЕО (слайд-шоу 9:16)" % a.dir)
        print("          записи на стене нет — фото в ВК нам закрыты")
    else:
        p = a.video if os.path.isabs(a.video) else os.path.join(HERE, a.video)
        if not os.path.exists(p):
            sys.exit("НЕТ ФАЙЛА: " + p)
        print("ЧТО     : ролик %s (%.1f МБ)"
              % (os.path.basename(p), os.path.getsize(p) / 1e6))
    print("КУДА    : %s" % ", ".join(
        "сообщество" if t == "group" else "личная страница" for t in targets))
    print("ТЕКСТ   : %d симв. (уйдёт в описание видео)" % len(cap))
    print("-" * 60)
    print(cap)
    print("-" * 60)

    if not a.send:
        print("[ПРОБНЫЙ ПРОГОН] Ничего не отправлено. Для публикации добавь --send")
        return

    env = load_env()
    # ПРИОРИТЕТ — вечный токен vk1.a. (Kate Mobile), тот же, что у vkcarousel.py.
    # Он со scope offline (не протухает) и умеет video.save — значит refresh-танец
    # с часовым vk2.a. больше не нужен. Оставляем старый путь как фолбэк, если
    # ключа KATE в .env нет.
    token = env.get("VK_USER_TOKEN_KATE")
    if token:
        print("токен ВК: VK_USER_TOKEN_KATE (vk1.a., вечный) — refresh не нужен")
    else:
        token = env.get("VK_USER_TOKEN")
        # VK-10: токен живёт час — обновляем сами через refresh, чтобы не входить в
        # браузер каждый день. Если refresh недоступен — работаем на текущем токене
        # (вдруг ещё жив), а совсем без токена честно просим запустить vk_auth.py.
        if env.get("VK_REFRESH_TOKEN"):
            fresh, err = refresh_access(env)
            if fresh:
                token = fresh
                print("токен ВК обновлён через refresh — вход в браузер не нужен")
            else:
                print("не удалось обновить токен:", err)
                print("пробую текущий VK_USER_TOKEN (если он ещё жив)...")
    if not token:
        sys.exit(
            "ВКОНТАКТЕ ЕЩЁ НЕ ПОДКЛЮЧЁН — публиковать нечем.\n"
            "Это делается один раз, около 3 минут, без регистраций:\n"
            "    python vk_auth.py --kate\n"
            "Помощник покажет ссылку, вы войдёте в ВК в своём браузере и\n"
            "вернёте ему адрес страницы, куда вас перекинет. Ключ он запишет сам.")

    path = slideshow_from(a.dir) if a.dir else (
        a.video if os.path.isabs(a.video) else os.path.join(HERE, a.video))

    # ⚠️ Умолчание `--to` — ОБА адреса. 05.08.2026 весь выпуск (r51, r52 и обе
    # карусели) ушёл только на личную страницу: команду позвали без `--to`, а
    # умолчанием был `user`. Сообщество не получило ничего, и заметила это
    # Валерия, а не код. Ограничивать доставку — осознанно, флагом.
    if "group" in targets and not env.get("VK_GROUP_ID"):
        sys.exit("НЕ ЗАДАН НОМЕР СООБЩЕСТВА ВК (VK_GROUP_ID) — в сообщество "
                 "публиковать некуда.\n"
                 "Он спрашивается в мастере установки («Номер сообщества "
                 "ВКонтакте») и лежит\n"
                 "в config/factory.config.json, а не в .env. Либо позовите с "
                 "--to user, если это осознанно.")

    name = cap.splitlines()[0]
    for t in targets:
        gid = env["VK_GROUP_ID"] if t == "group" else None
        print("\n--- %s ---" % ("СООБЩЕСТВО" if t == "group" else "ЛИЧНАЯ СТРАНИЦА"))
        owner, vid = upload_video(token, path, name, cap, group_id=gid)
        print("   видео залито: video%s_%s (в Клипах)" % (owner, vid))
        link = post_video_wall(token, owner, vid, cap, group_id=gid,
                               publish_at=publish_at)
        print("   ПОСТ НА СТЕНЕ:", link)

    print("\nПОСМОТРИ ГЛАЗАМИ. Лог не доказательство — 17.07 он дважды "
          "отрапортовал успех, а ролик лежал на боку.")


if __name__ == "__main__":
    main()
