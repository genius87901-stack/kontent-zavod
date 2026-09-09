# -*- coding: utf-8 -*-
"""
ПОЛУЧЕНИЕ ПОЛЬЗОВАТЕЛЬСКОГО ТОКЕНА ВК ЧЕРЕЗ VK ID (OAuth 2.1 + PKCE).

ЗАЧЕМ ЭТО ВООБЩЕ. Ключ сообщества (тот, что берётся за пять кликов в Управлении)
прав на ВИДЕО не даёт — в списке галочек его просто нет: управление, сообщения,
фото, документы, истории, стена, товары. Значит видео и Клипы в группу, а также
что-либо на личную страницу, требуют ПОЛЬЗОВАТЕЛЬСКОГО токена.

ПОЧЕМУ НЕ СТАРЫМ ПРОСТЫМ СПОСОБОМ. Раньше standalone-приложение отдавало токен
прямо в адресной строке (`oauth.vk.com/blank.html`, Implicit Flow) — минутное
дело. С июня 2024 ВК перевёл выдачу на VK ID, а в апреле 2026 отключил токены
VK Admin, которыми обходились. Осталась полноценная авторизация с обменом кода.

⚠️ ЧЕСТНО О НАДЁЖНОСТИ ЭТОГО ФАЙЛА. `dev.vk.com` из окружения ПК Chirkova не
открывается, и флоу собран по вторичным источникам, а не по официальной
документации. Поэтому скрипт написан так, чтобы ПАДАТЬ ГРОМКО и показывать
сырой ответ ВК: если что-то не сойдётся, мы увидим их формулировку, а не будем
гадать. Не считать этот файл проверенным, пока не отработает живьём.

ЧТО НУЖНО ЗАРАНЕЕ:
  1. Завести standalone-приложение на dev.vk.com -> Мои приложения -> Создать.
  2. В настройках приложения прописать доверенный redirect_uri. По умолчанию
     скрипт ждёт http://localhost:8080/callback — впиши ровно эту строку.
     Если ВК не примет localhost, запускай с --paste (см. ниже).
  3. Взять ID приложения и положить в .env:
         VK_APP_ID=12345678

ДВА РЕЖИМА:
  обычный  — поднимаем локальный сервер, ВК сам возвращает код в него.
  --paste  — сервер не поднимаем: ты копируешь адресную строку из браузера
             целиком и вставляешь сюда. Работает с ЛЮБЫМ redirect_uri, в том
             числе с blank.html, и потому это запасной путь, если localhost
             в приложении прописать не дают.

ТОКЕН В ЧАТ НЕ ПОПАДАЕТ. Скрипт сам дописывает VK_USER_TOKEN в .env и никогда
не печатает его целиком — только длину.

ЗАПУСК (путь VK ID — ЗАПАСНОЙ, только видео):
    python vk_auth.py
    python vk_auth.py --paste
    python vk_auth.py --scope "wall photos video groups offline"

🟢 ОСНОВНОЙ ПУТЬ КЛИЕНТА — НЕ ЭТОТ, А `--kate` (см. блок про Kate Mobile ниже).
Он даёт ключ vk1.a., который умеет карусель, видео, стену и сторис, живёт
практически вечно и не требует ни VK_APP_ID, ни регистрации на dev.vk.com:
    python vk_auth.py --kate                      # показать ссылку входа
    python vk_auth.py --kate-finish "<адрес>"     # записать ключ из адреса
Обе команды в сеть НЕ ХОДЯТ вовсе и ничего не публикуют.
"""
import argparse
import base64
import hashlib
import http.server
import os
import secrets
import sys
import threading
import urllib.parse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
AUTHORIZE = "https://id.vk.com/authorize"
TOKEN = "https://id.vk.com/oauth2/auth"
REDIRECT = "https://aevkone.github.io/karusel/callback"
PORT = 8080
# Права: стена (публикация), фото, видео, группы (постинг от имени сообщества),
# offline (токен не протухает через несколько часов).
SCOPE = "wall photos video groups offline"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ КЛИЕНТА: `--kate` / `--kate-finish`.
#
# ЗАЧЕМ ОТДЕЛЬНЫЙ РЕЖИМ, ЕСЛИ ВЫШЕ УЖЕ ЕСТЬ VK ID. Потому что VK ID выдаёт токен
# формата vk2.a., а он УМЕЕТ ТОЛЬКО ВИДЕО: `wall.post` отвечает 1051, фото на
# стену — 15 (замеры 22–23.07, VK_RESEARCH.md). То есть карусель на нём
# невозможна в принципе. Плюс он живёт час и требует связки
# VK_REFRESH_TOKEN + VK_APP_ID + VK_DEVICE_ID, чтобы продлеваться, а сам
# VK_APP_ID клиент может получить только зарегистрировавшись разработчиком на
# dev.vk.com. Для нетехнического человека это тупик, и именно в него он упирался.
#
# Классический путь (им пользуется весь рунет): авторизоваться через публичный
# client_id официального приложения Kate Mobile — у него право `wall` одобрено
# ВК давно. На выходе токен vk1.a., который умеет фото-альбом на стене, видео,
# клипы, стену и сторис, а со `scope offline` практически не протухает.
# Регистрировать ничего не надо, ставить Kate Mobile тоже — используется только
# её публичный номер.
#
# ПОЧЕМУ ЗДЕСЬ НЕТ НИ ОДНОГО ЗАПРОСА В СЕТЬ. Это Implicit Flow: токен приезжает
# в ЯКОРЕ адреса (`#access_token=…`), который браузер сереверу не отправляет
# вовсе. Обменивать нечего — `--kate` только печатает ссылку, `--kate-finish`
# только разбирает строку и пишет `.env`. Ни одного обращения к api.vk.com, а
# значит режим целиком проверяется тестами и не может ничего опубликовать.
#
# ПОЧЕМУ АДРЕС — АРГУМЕНТ, А НЕ `input()`. Шаг зовёт помощник установки, а по
# контракту модулей все вопросы задаёт мастер, а не код. Аргументом этот шаг
# встраивается в мастер; `input()` сделал бы его непригодным для автоматизации.
KATE_AUTHORIZE = "https://oauth.vk.com/authorize"
KATE_CLIENT_ID = "2685278"          # публичный номер приложения Kate Mobile
KATE_REDIRECT = "https://oauth.vk.com/blank.html"
KATE_SCOPE = "wall,photos,video,offline,groups"
KATE_ENV_KEY = "VK_USER_TOKEN_KATE"   # канон, см. modules/publishing/config_reader.py


def load_env(path=ENV):
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def save_env(key, value, path=ENV):
    """Дописать/заменить строку в .env, не трогая остальные ключи."""
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


def pkce():
    """code_verifier и code_challenge = base64url(sha256(verifier)) без padding."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class Catcher(http.server.BaseHTTPRequestHandler):
    """Ловит редирект от ВК и запоминает параметры адреса."""
    result = {}

    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        Catcher.result = dict(urllib.parse.parse_qsl(q))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in Catcher.result
        msg = ("Готово. Возвращайся в терминал." if ok
               else "ВК вернул ответ без кода. Смотри терминал.")
        self.wfile.write(f"<h3>{msg}</h3>".encode("utf-8"))

    def log_message(self, *args):
        pass          # не засорять вывод строчками веб-сервера


def wait_for_code(timeout=300):
    srv = http.server.HTTPServer(("localhost", PORT), Catcher)
    srv.timeout = timeout
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    t.join(timeout)
    srv.server_close()
    return Catcher.result


def ask_paste():
    print("\nПосле входа браузер уйдёт на адрес с кодом. Скопируй адресную "
          "строку ЦЕЛИКОМ и вставь сюда.")
    raw = input("Адрес: ").strip()
    if not raw:
        sys.exit("Пусто — нечего разбирать.")
    q = urllib.parse.urlparse(raw).query
    d = dict(urllib.parse.parse_qsl(q))
    if not d:
        sys.exit(f"В адресе нет параметров. Получено: {raw[:200]}")
    return d


def kate_link(scope=KATE_SCOPE, redirect=KATE_REDIRECT):
    """Ссылка входа в ВК через приложение Kate Mobile. В сеть не ходит.

    `revoke=1` — чтобы ВК показал экран согласия ЗАНОВО, даже если человек уже
    когда-то давал доступ. Без него ВК молча переиспользует старое согласие со
    старым набором прав, и на выходе оказывается токен, которому нельзя ни
    залить фото, ни написать на стену, — а понять это можно только по отказу
    при публикации.
    """
    # safe=",:/" — НЕ косметика. Единственная ссылка, про которую известно, что
    # она работает (ученикам/10_ВК_АВТОВЫГРУЗКА.md, тот же адрес обошёл весь
    # рунет), несёт запятые в scope и адрес возврата открытым текстом. Стандарт
    # позволяет и `%2C`, но проверять на клиенте догадку «ВК наверняка
    # раскодирует» нечем: цена ошибки — человек идёт по ссылке, получает ключ
    # без прав и узнаёт об этом только на первой публикации. Отличий от
    # проверенного адреса не оставляем ни одного.
    return KATE_AUTHORIZE + "?" + urllib.parse.urlencode({
        "client_id": KATE_CLIENT_ID,
        "scope": scope,
        "response_type": "token",
        "redirect_uri": redirect,
        "display": "page",
        "v": "5.199",
        "revoke": 1,
    }, safe=",:/")


def kate_parse(raw):
    """Разобрать адрес, на который ВК перекинул браузер. В сеть не ходит.

    Токен лежит в ЯКОРЕ (`#access_token=…`), но клиент вполне может прислать
    адрес, где параметры оказались в запросе (`?`) — например скопировав его из
    другого места. Смотрим оба; ошибку ВК (`error=…`) распознаём отдельно, иначе
    человек увидит невнятное «нет ключа» там, где ВК прямым текстом объяснил
    причину.

    Возвращает словарь параметров. Бросает ValueError с человеческим текстом.
    """
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("Адрес пустой — вставьте строку из адресной строки "
                         "браузера целиком.")
    parts = urllib.parse.urlparse(raw)
    got = dict(urllib.parse.parse_qsl(parts.fragment))
    got.update({k: v for k, v in urllib.parse.parse_qsl(parts.query)
                if k not in got})
    if got.get("error"):
        raise ValueError(
            "ВКонтакте отказал: %s. %s\nЧаще всего это значит, что на экране "
            "согласия нажали «Отмена». Пройдите вход ещё раз."
            % (got.get("error"), got.get("error_description", "")))
    if not got.get("access_token"):
        raise ValueError(
            "В этом адресе ключа нет. Нужен адрес ПУСТОЙ БЕЛОЙ страницы, на "
            "которую перекинуло ПОСЛЕ нажатия «Разрешить» — в нём есть кусок "
            "«#access_token=vk1.a...». Скопируйте адресную строку целиком "
            "(Ctrl+L, Ctrl+C) и пришлите ещё раз.\nПолучено начало: %s"
            % raw[:80])
    return got


def kate_finish(raw, path=ENV):
    """Достать ключ из адреса и записать его в `.env`. В сеть не ходит.

    Ключ целиком НЕ печатается нигде — ни в консоль, ни в переписку: он даёт
    полный доступ к стене. Печатаем длину и первые символы формата.
    """
    got = kate_parse(raw)
    token = got["access_token"]
    if not token.startswith("vk1."):
        # Не отказ: ВК может однажды сменить префикс. Но промолчать нельзя —
        # именно неверный ФОРМАТ токена и был корнем всей путаницы с 1051.
        print("⚠️ Ключ не похож на vk1.a. (начало: %s...). Карусель и стена "
              "работают только с таким форматом — если публикация ответит "
              "ошибкой 1051 или 15, дело в этом." % token[:7])
    replaced = save_env(KATE_ENV_KEY, token, path)
    print("КЛЮЧ ВК ПОЛУЧЕН. Длина %d симв., сам ключ не печатаю." % len(token))
    print("Записан в .env как %s (%s)."
          % (KATE_ENV_KEY, "заменён" if replaced else "добавлен"))
    if got.get("user_id"):
        print("Аккаунт ВК (user_id): %s" % got["user_id"])
    if got.get("expires_in") == "0":
        print("Срок действия: бессрочный — сработало право offline. "
              "Это то, что нужно: заново входить не придётся.")
    elif got.get("expires_in"):
        print("⚠️ Срок действия: %s сек. Право offline НЕ сработало — ключ "
              "протухнет и публикация встанет с ошибкой 5. Пройдите вход ещё "
              "раз по ссылке из `python vk_auth.py --kate`." % got["expires_in"])
    print("")
    print("Дальше: скажите помощнику «проверь готовность публикации» "
          "(python -m publishing.doctor).")
    return got


def kate_intro(link):
    """Что клиент видит на экране. Отдельной функцией — её печатает и мастер."""
    return "\n".join([
        "=" * 68,
        "ПОДКЛЮЧЕНИЕ ВКОНТАКТЕ. Один раз, около 3 минут.",
        "Ничего не публикуется и никуда не отправляется — это только вход.",
        "=" * 68,
        "",
        "1. ВЫКЛЮЧИТЕ VPN. ВК запоминает, с какого подключения выдан ключ;",
        "   если адрес скачет, публикация потом будет отваливаться.",
        "",
        "2. Откройте эту ссылку в СВОЁМ браузере — том, где вы уже вошли в ВК:",
        "",
        link,
        "",
        "3. ВК покажет экран «Kate Mobile запрашивает доступ». Нажмите",
        "   «Разрешить». Экран может перечислить только «Общую информацию» —",
        "   это нормально, настоящие права зашиты в ссылке.",
        "   Ставить Kate Mobile НЕ нужно: используется только её номер.",
        "",
        "4. Браузер перекинет на ПУСТУЮ БЕЛУЮ страницу. Нужен её АДРЕС.",
        "   Щёлкните в адресную строку, выделите всё (Ctrl+A) и скопируйте",
        "   (Ctrl+C). Внутри будет кусок «#access_token=vk1.a...».",
        "",
        "5. Отдайте этот адрес помощнику словами «вот ключ ВК» — он выполнит:",
        '       python vk_auth.py --kate-finish "<адрес>"',
        "   и запишет ключ сам. Ключ целиком нигде не печатается.",
        "",
        "⚠️ Этот адрес = полный доступ к вашей стене. Никому, кроме своего",
        "   помощника, его не пересылайте и в открытые чаты не вставляйте.",
        "=" * 68,
    ])


STASH = os.path.join(HERE, ".vk_pkce.json")


def stash_save(data):
    """Сохранить verifier/state между двумя запусками (режим --start/--finish).

    Нужен, потому что PKCE требует ОДИН И ТОТ ЖЕ code_verifier на обоих шагах,
    а Валерия ходит по ссылке в браузере между ними. Файл служебный и живёт
    минуты; в .gitignore он не нужен — verifier без кода бесполезен, но и
    хранить его дольше незачем, --finish его удаляет.
    """
    import json
    with open(STASH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def stash_load():
    import json
    if not os.path.exists(STASH):
        sys.exit("Нет .vk_pkce.json — сначала запусти с --start")
    with open(STASH, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Токен ВК через VK ID (PKCE)")
    ap.add_argument("--start", action="store_true",
                    help="только напечатать ссылку и запомнить verifier")
    ap.add_argument("--finish", metavar="URL",
                    help="обменять код из адреса, куда вернул ВК")
    ap.add_argument("--paste", action="store_true",
                    help="не поднимать сервер: код вставляется руками")
    ap.add_argument("--scope", default=SCOPE, help=f"права (по умолчанию: {SCOPE})")
    ap.add_argument("--redirect", default=REDIRECT,
                    help="redirect_uri, ровно как в настройках приложения")
    ap.add_argument("--kate", action="store_true",
                    help="ОСНОВНОЙ путь клиента: показать ссылку входа через "
                         "Kate Mobile (ключ vk1.a. — умеет карусель, видео, "
                         "стену и сторис). Ничего не отправляет")
    ap.add_argument("--kate-finish", metavar="URL",
                    help="записать ключ из адреса, куда перекинул ВК после "
                         "«Разрешить». В сеть не ходит")
    a = ap.parse_args()

    # Ветки Kate идут ДО проверки VK_APP_ID: им номер приложения не нужен вовсе,
    # и требовать его значило бы вернуть клиента ровно в тот тупик, ради выхода
    # из которого эти ветки и заведены.
    if a.kate:
        print(kate_intro(kate_link()))
        return
    if a.kate_finish:
        try:
            kate_finish(a.kate_finish)
        except ValueError as ex:
            sys.exit(str(ex))
        return

    env = load_env()
    app_id = env.get("VK_APP_ID")
    if not app_id:
        sys.exit(
            "В .env нет VK_APP_ID.\n"
            "Заведи standalone-приложение на dev.vk.com, возьми его ID и допиши:\n"
            "    VK_APP_ID=12345678\n"
            f"А в настройках приложения пропиши redirect_uri: {a.redirect}"
        )

    # ── режим --finish: verifier берём из файла, ссылку не строим ──
    if a.finish:
        saved = stash_load()
        verifier, state = saved["verifier"], saved["state"]
        redirect = saved["redirect"]
        q = urllib.parse.urlparse(a.finish).query
        got = dict(urllib.parse.parse_qsl(q))
        if not got.get("code"):
            sys.exit(f"В адресе нет code. Пришло: {got}")
        if got.get("state") and got["state"] != state:
            sys.exit("state не совпал — адрес от другого запроса. Начни с --start.")
        exchange(app_id, got, verifier, redirect, state)
        try:
            os.remove(STASH)
        except OSError:
            pass
        return

    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    url = AUTHORIZE + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": app_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": a.redirect,
        "state": state,
        "scope": a.scope,
    })

    print("Открой в браузере и войди под своим аккаунтом ВКонтакте:\n")
    print(url)
    print()

    if a.start:
        stash_save({"verifier": verifier, "state": state, "redirect": a.redirect})
        print("Ссылка выдана. Сходи по ней, потом верни адрес из строки браузера:")
        print('    python vk_auth.py --finish "<адрес целиком>"')
        return

    if a.paste:
        got = ask_paste()
    else:
        print(f"Жду возврата на {a.redirect} (до 5 минут)...")
        print("Если ВК не принял localhost — прерви и запусти с --paste")
        got = wait_for_code()

    if not got:
        sys.exit("Ответа не дождались. Попробуй режим --paste.")
    if "error" in got:
        sys.exit(f"ВК вернул ошибку: {got}")
    code = got.get("code")
    if not code:
        sys.exit(f"В ответе нет code. Пришло: {got}")
    if got.get("state") and got["state"] != state:
        sys.exit("state не совпал — ответ пришёл не на наш запрос. Повтори.")

    exchange(app_id, got, verifier, a.redirect, state)


def exchange(app_id, got, verifier, redirect, state):
    """Обменять код на токен и рассказать, какие права ВК реально выдал."""
    # device_id ВК передаёт вместе с кодом и требует обратно при обмене.
    payload = {
        "grant_type": "authorization_code",
        "code": got["code"],
        "code_verifier": verifier,
        "client_id": app_id,
        "redirect_uri": redirect,
        "state": state,
    }
    if got.get("device_id"):
        payload["device_id"] = got["device_id"]

    r = requests.post(TOKEN, data=payload, timeout=60)
    try:
        d = r.json()
    except ValueError:
        sys.exit(f"ВК ответил не JSON ({r.status_code}):\n{r.text[:600]}")
    if "access_token" not in d:
        # Показываем СЫРОЙ ответ: флоу собран по вторичным источникам, и их
        # формулировка ошибки полезнее любых моих догадок.
        sys.exit(f"Токен не выдан. HTTP {r.status_code}, ответ ВК:\n{d}")

    # ГЛАВНОЕ ЗАМЕР 22.07: какие права ВК выдал НА САМОМ ДЕЛЕ. Экран согласия
    # показал только «Общая информация», хотя запрошены wall/photos/video —
    # но экран рисует ярлыки, а правду говорит поле scope в ответе на токен.
    print("ВЫДАННЫЙ SCOPE:", repr(d.get("scope")))

    token = d["access_token"]
    replaced = save_env("VK_USER_TOKEN", token)
    print(f"\nТОКЕН ПОЛУЧЕН. Длина {len(token)} симв., сам ключ не печатаю.")
    print(f"Записан в .env как VK_USER_TOKEN ({'заменён' if replaced else 'добавлен'}).")
    if d.get("user_id"):
        print(f"user_id: {d['user_id']}")
    if d.get("expires_in") == 0:
        print("Срок: бессрочный (сработал offline).")
    elif d.get("expires_in"):
        print(f"Срок: {d['expires_in']} сек — offline не сработал, "
              f"токен придётся обновлять.")
    if d.get("refresh_token"):
        save_env("VK_REFRESH_TOKEN", d["refresh_token"])
        print("refresh_token тоже сохранён.")
    # device_id нужен для авто-обновления токена (grant_type=refresh_token в
    # vkpost.py). Приходит вместе с кодом из редиректа ВК — сохраняем его тоже,
    # иначе refresh у VK ID не примут и придётся входить в браузер каждый час.
    if got.get("device_id"):
        save_env("VK_DEVICE_ID", got["device_id"])
        print("device_id сохранён (нужен для авто-обновления токена).")
    print("\nДальше: python vkpost.py --video ... --to group --send")


if __name__ == "__main__":
    main()
