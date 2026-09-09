# -*- coding: utf-8 -*-
"""ЗАПИСЬ НА СТЕНУ ВК — последний незакрытый кусок по видео.

ЗАЧЕМ ОТДЕЛЬНЫМ ФАЙЛОМ И ЗАЧЕМ СРАЗУ ПОСЛЕ ТОКЕНА. Замер 22.07 показал, что
внешний IP Валерии МЕНЯЕТСЯ между запросами (две заливки подряд ушли с
176.97.210.246 и 31.76.113.252 — VPN с ротацией). ВК привязывает токен к
адресу выдачи: `video.save` этот адрес не проверяет и работает всегда, а
`wall.post` проверяет и отбивает токен ошибкой 5. Поэтому запись на стене
должна идти В ТУ ЖЕ СЕКУНДУ, что и обмен кода на токен — этим занят
`ВК_СТЕНА.bat`, который зовёт сначала vk_auth.py, потом сразу этот файл.

ЧТО ЕЩЁ НЕ ИЗВЕСТНО. Даже при совпадении IP `wall.post` может отбиться по
правам: scope у нового VK ID приложения приходит пустой
(`vkid.personal_info offline`). Узнать косвенно нельзя — только живой
попыткой. Если придёт ошибка 15 или 214, значит право `wall` действительно
не выдаётся, и путь на стену закрыт окончательно (видео при этом остаётся
рабочим — оно идёт мимо стены).

ПРЕДОХРАНИТЕЛЬ. Без --send скрипт ничего не публикует, только показывает.
Публикация необратима и видна подписчикам сразу.

ЗАПУСК:
    python vk_wall_post.py --attach video6720515_456244198 --text vk_post_r5.txt
    python vk_wall_post.py --attach video6720515_456244198 --text vk_post_r5.txt --send
"""
import argparse
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
V = "5.199"

# Единый ридер настроек — общий с modules/publishing/doctor.py. См. шапку
# modules/publishing/config_reader.py.
sys.path.append(os.path.join(HERE, "modules"))
try:
    from publishing import config_reader
except Exception:          # обрезанная копия без modules/ — работаем как раньше
    config_reader = None

# Общий текст ошибок ВК — см. пояснение в шапке vkpost.py.
try:
    from vkcarousel import vk_error_text
except Exception:
    vk_error_text = None


def load_env():
    """Настройки ВК: config/factory.config.json, поверх — .env.

    ⚠️ ДВА ФАЙЛА, А НЕ ОДИН: VK_GROUP_ID мастер установки кладёт в конфиг (это
    номер, не секрет), а токен — в `.env`. Читая только `.env`, скрипт падал
    здесь KeyError'ом на настроенной коробке.
    """
    if config_reader is not None:
        return config_reader.merged(start=HERE)
    env = {}
    with open(os.path.join(HERE, ".env"), encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def main():
    ap = argparse.ArgumentParser(description="Запись на стену ВК")
    ap.add_argument("--attach", required=True,
                    help="вложение, напр. video6720515_456244198")
    ap.add_argument("--text", required=True, help="файл с текстом записи")
    ap.add_argument("--group", action="store_true",
                    help="на стену сообщества, а не на личную")
    ap.add_argument("--send", action="store_true",
                    help="ДЕЙСТВИТЕЛЬНО опубликовать (без него — только показать)")
    a = ap.parse_args()

    env = load_env()
    # ПРИОРИТЕТ — канонический ключ vk1.a. (VK_USER_TOKEN_KATE). Раньше здесь
    # брался ТОЛЬКО VK_USER_TOKEN (vk2.a.), а ему `wall.post` закрыт по формату
    # (1051/15) — то есть на полностью подключённой коробке этот скрипт был
    # обречён отбиться, хотя рабочий ключ лежал рядом.
    token = env.get("VK_USER_TOKEN_KATE") or env.get("VK_USER_TOKEN")
    if not token:
        sys.exit(
            "ВКОНТАКТЕ ЕЩЁ НЕ ПОДКЛЮЧЁН — публиковать нечем.\n"
            "Один раз, около 3 минут: python vk_auth.py --kate")

    path = a.text if os.path.isabs(a.text) else os.path.join(HERE, a.text)
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()

    params = {"message": text, "attachments": a.attach}
    if a.group:
        # у сообщества стена принадлежит отрицательному id, и постим от имени группы
        if not env.get("VK_GROUP_ID"):
            sys.exit("НЕ ЗАДАН НОМЕР СООБЩЕСТВА ВК (VK_GROUP_ID) — на стену "
                     "сообщества писать некуда.\n"
                     "Он спрашивается в мастере установки («Номер сообщества "
                     "ВКонтакте») и лежит\n"
                     "в config/factory.config.json, а не в .env.")
        params["owner_id"] = "-" + str(env["VK_GROUP_ID"])
        params["from_group"] = 1

    print("=" * 60)
    print("КУДА:", "стена сообщества" if a.group else "личная стена")
    print("ВЛОЖЕНИЕ:", a.attach)
    print("ТЕКСТ (%d симв.):" % len(text))
    print("-" * 60)
    print(text)
    print("-" * 60)

    if not a.send:
        print("ПРОБНЫЙ ПРОГОН. Ничего не отправлено — нужен --send.")
        return

    params.update({"access_token": token, "v": V})
    r = requests.post("https://api.vk.com/method/wall.post",
                      data=params, timeout=60).json()
    if "error" in r:
        e = r["error"]
        code = e.get("error_code")
        # Тот же текст, что у vkcarousel.py/vkpost.py: одна беда — один совет.
        # Нет vkcarousel рядом (обрезанная копия) — прежний короткий разбор.
        if vk_error_text is not None:
            print(vk_error_text(code, e.get("error_msg")))
        else:
            print("ОШИБКА %s: %s" % (code, e.get("error_msg")))
            if code == 5:
                print("-> Ключ ВК больше не принимают. python vk_auth.py --kate")
            elif code == 15:
                print("-> Доступ запрещён: права `wall` у приложения нет.")
            elif code == 214:
                print("-> Публикация на стене запрещена настройками страницы.")
        print("ЗАПИСИ НЕ СОЗДАНО.")
        return

    post_id = r["response"].get("post_id")
    owner = params.get("owner_id", env.get("VK_USER_ID", "6720515"))
    print("ОПУБЛИКОВАНО. post_id=%s" % post_id)
    print("ССЫЛКА: https://vk.com/wall%s_%s" % (owner, post_id))
    print("Посмотри глазами — лог не доказательство.")


if __name__ == "__main__":
    main()
