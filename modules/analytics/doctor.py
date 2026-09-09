# -*- coding: utf-8 -*-
"""doctor модуля analytics (аналитика опубликованного контента + рекомендации).

Печатает ПО-РУССКИ человеческим языком, что готово и что поправить. Без
стектрейсов.

Коды выхода — СТЕПЕНЬ БЕДЫ (CONTRACT.md §2, BOX-51):
    0 — модуль полностью работоспособен: метрики берутся из ВАШИХ вышедших
        постов (живой сбор настроен), движок «метрика→совет» отрабатывает.
    2 — УРЕЗАННЫЙ РЕЖИМ: советы считаются, но по встроенному образцу, а не по
        вашим постам (не заполнен ключ Upload-Post / выбран источник sample),
        либо живые данные давно не обновлялись. Бросать всё и чинить не нужно.
    1 — НЕ ГОТОВ: главного пути нет (нечего анализировать, сквозной прогон
        падает, хранилище повреждено) ЛИБО сорван предохранитель платного
        сервиса, ЛИБО сама проверка не смогла отработать.

До 11.08.2026 доктор печатал человеку «Это НЕ поломка» и всё равно уходил с
кодом 1 — владельцу летела красная тревога «🔴 СРОЧНО» на ненастроенный ключ.
Теперь такие случаи собираются в отдельный список `limits` и дают код 2.

Запуск (оба варианта):
    python -m analytics.doctor
    python modules/analytics/doctor.py
"""
from __future__ import annotations

import sys

try:
    from . import (cache, config, history, live, model, paths, providers,
                   published, recommender, upload_post)
except ImportError:  # запуск файлом напрямую
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from analytics import (cache, config, history, live, model, paths,
                           providers, published, recommender, upload_post)


def check():
    """Проверка модуля. Возвращает три списка:

        problems — модуль НЕ готов (код 1): главного пути нет либо сорван
                   предохранитель платного сервиса. Красная тревога владельцу.
        limits   — УРЕЗАННЫЙ РЕЖИМ (код 2): считаем, но по образцу, а не по
                   вашим постам. Обычная строка суточной сводки.
        lines    — просто наблюдения, на код возврата не влияют.
    """
    problems, limits, lines = [], [], []

    # 1. Пороги правил. Все с валидными умолчаниями — модуль работает без настроек.
    thr = config.thresholds()
    if thr["retention_pct"] <= 0 or thr["engagement_rate_pct"] <= 0:
        problems.append("Пороги правил заданы некорректно: %s. Нужны "
                        "положительные числа. Пока это так, движок советов "
                        "не может отличить хороший пост от слабого." % thr)
    else:
        lines.append("OK  Пороги: досмотр ≥%d%%, переходы ≥%.1f%%, сохранения "
                     "≥%.1f%%, вовлечённость ≥%.1f%%, охват не ниже медианы на %d%%."
                     % (thr["retention_pct"], thr["ctr_pct"], thr["save_rate_pct"],
                        thr["engagement_rate_pct"], thr["reach_drop_pct"]))

    # 2. Источник метрик и ПРЕДОХРАНИТЕЛЬ платного сервиса. Честно печатаем:
    #    какой источник, есть ли ключ Upload-Post, что доступно. Ключ НЕ печатаем.
    #
    # ГЛАВНЫЙ ВОПРОС ЭТОГО МОДУЛЯ: советы считаются по ВАШИМ вышедшим постам или
    # по встроенному демо-образцу? Образец — рабочее, но урезанное состояние:
    # цифры в отчёте не про ваш аккаунт. Это код 2, а не 0 и не 1.
    src = config.metrics_source()
    key_present = upload_post.has_key()
    live_wanted = src in config.LIVE_SOURCES
    session = live.LiveSession()
    live_ready, live_why = session.available()

    if live_wanted and live_ready:
        lines.append("OK  Источник метрик: ваши посты через живой Upload-Post "
                     "(analytics_metrics_source=%s), профиль %r. Боевой запрос "
                     "doctor НЕ делает — он платный; снять метрики живьём: "
                     "python -m analytics.cli ingest --live." % (src, session.user))
    elif live_wanted:
        limits.append("Живой сбор метрик запрошен в настройках (%s), но не "
                      "настроен: %s. Пока этого нет, отчёты и советы считаются по "
                      "ВСТРОЕННОМУ ОБРАЗЦУ — это демонстрационные посты, а не ваш "
                      "аккаунт: числа в сводке будут не про вас. Всё остальное "
                      "работает, платный аккаунт не трогаем." % (src, live_why))
    else:
        tail = ("Ключ и профиль Upload-Post на месте — не хватает одной "
                "настройки analytics_metrics_source=upload_post."
                if live_ready else
                "Чтобы включить: настройка analytics_metrics_source=upload_post "
                "плюс ключ Upload-Post (%s)." % live_why)
        limits.append("Метрики берутся из встроенного ОБРАЗЦА (демо), а не из "
                      "ваших вышедших постов: советы вы увидите, но они будут "
                      "посчитаны по чужим демо-числам, а не по вашему аккаунту. "
                      + tail)

    # Предохранитель: живой запрос БЕЗ ключа обязан деградировать на образец
    #    БЕЗ сети/платного вызова (LiveSourceUnavailable → образец из карточки).
    safety_ok = False
    if key_present:
        # Ключ есть — сеть в doctor не дёргаем (это платный вызов). Достаточно, что
        # без идентификатора поста живой сбор не пойдёт в сервис вслепую.
        try:
            providers.PROVIDERS["instagram"].fetch_live({"post_id": "x"})
        except providers.LiveSourceUnavailable:
            safety_ok = True  # нет request_id → в платный вызов не пошли
        except Exception:
            safety_ok = False
        if safety_ok:
            lines.append("OK  Предохранитель: без request_id/platform_post_id "
                         "живой вызов в Upload-Post не уходит (нужен явный ключ "
                         "связи поста). Платный аккаунт вслепую не жжётся.")
        else:
            problems.append("Живой сбор пошёл в Upload-Post без идентификатора "
                            "поста — так нельзя, это трата чужого аккаунта.")
    else:
        # Ключа нет: live-запрос обязан вернуть образец, а не исключение, и без сети.
        res = providers.fetch_metrics(
            {"post_id": "safety", "platform": "instagram",
             "raw": {"reach": 100, "impressions": 120}}, live=True)
        safety_ok = res.get("ok") and res.get("source") == "sample"
        if safety_ok:
            lines.append("OK  Предохранитель: запрошен живой источник без ключа → "
                         "модуль вернул образец (source=sample), платный вызов не "
                         "сделан, сети нет.")
        else:
            problems.append("Живой источник без ключа повёл себя неверно: %r. "
                            "Ожидался безопасный откат на образец." % res)

    # 2б. КОНФИГУРАЦИЯ живого сбора — проверяем БЕЗ единого сетевого вызова.
    #     Живой запрос здесь делать нельзя: он платный и требует явного согласия
    #     человека. Поэтому смотрим только на то, что можно проверить локально:
    #     форму ключа, наличие профиля, журнал публикаций, свежесть кэша.
    lines.append("i   Ключ Upload-Post: %s (сам ключ не печатается)."
                 % upload_post.key_shape())
    if live_ready:
        lines.append("OK  Конфигурация живого сбора полная: ключ и профиль %r на "
                     "месте." % session.user)
    else:
        # Раньше здесь печаталось «Это НЕ поломка», а модуль всё равно мог уйти с
        # кодом 1 — ровно тот дефект, ради которого в контракте появился код 2.
        # Теперь сама деградация уже записана выше в limits (код 2), а тут только
        # техническая расшифровка причины.
        lines.append("i   Живой сбор не настроен: %s." % live_why)

    # Журнал публикаций — источник request_id для живой привязки постов.
    pub = published.summary()
    if pub["exists"] and pub["rows"]:
        lines.append("OK  Шов с публикацией на месте: %d записей, %d публикаций, "
                     "площадки %s. По этим request_id и пойдёт живой сбор."
                     % (pub["rows"], pub["requests"],
                        ", ".join(pub["platforms"]) or "—"))
    else:
        lines.append("i   Журнала публикаций (%s) ещё нет — реальных отправок не "
                     "было. Живому сбору не за что зацепиться, работаем на образце."
                     % paths.published_log_path())

    # Последнее УСПЕШНОЕ обновление живых данных — главный признак «работает».
    stamp = cache.last_success()
    if stamp:
        age = cache.age_hours(stamp)
        lines.append("OK  Последнее успешное обновление живых данных: %s (%s ч "
                     "назад). Есть кэш — при обрыве сети модуль отдаст его, а не "
                     "пустоту." % (stamp, age))
        if age is not None and age > 24 * 14:
            # Устаревшие данные — не поломка: советы считаются, но по старым
            # числам. Человеку не нужно бросать всё прямо сейчас → код 2.
            limits.append("Живые данные не обновлялись %.0f дней — советы "
                          "считаются по старым числам и могут отстать от того, "
                          "что происходит в аккаунте сейчас. Проверьте ключ и "
                          "сеть: python -m analytics.cli status" % (age / 24))
    else:
        lines.append("i   Живой сбор ещё ни разу не срабатывал — кэша нет. "
                     "Нормально до первого запуска ingest --live.")

    # 2в. Офлайн-устойчивость. Модуль обязан работать без сети — проверяем это
    #     подменой транспорта на «сети нет», НЕ трогая сеть по-настоящему.
    def _offline(path, params, key, timeout):
        raise upload_post.UploadPostOffline("проверка офлайна (сеть не трогали)")

    prev = upload_post.set_transport(_offline)
    try:
        res = providers.fetch_metrics(
            {"post_id": "offline_check", "platform": "instagram",
             "request_id": "req_offline_check",
             "raw": {"reach": 100, "impressions": 120}}, live=True)
        offline_ok = res.get("ok") and res.get("source") in ("sample",
                                                             "upload_post_cache")
    except Exception as ex:
        offline_ok, res = False, ex
    finally:
        upload_post.set_transport(prev)
    if offline_ok:
        lines.append("OK  Офлайн-устойчивость: при полном отсутствии сети живой "
                     "сбор не падает, а честно отдаёт %s с причиной. Офлайн-"
                     "установка не ломается." % res.get("source"))
    else:
        problems.append("Без сети живой сбор повёл себя неверно: %r. Ожидался "
                        "безопасный откат, а не сбой." % (res,))

    # 3. Образец метрик читается и провайдеры его нормализуют.
    posts = providers.load_sample()
    if not posts:
        # Это код 1, а не 2: образец — единственные данные, на которых модуль
        # умеет что-то показать до первого живого сбора. Без него анализировать
        # нечего и сама проверка движка советов не может отработать.
        problems.append("Образец метрик (файл %s в папке analytics) не найден или "
                        "пуст — анализировать нечего, отчёт будет пустым. Файл "
                        "входит в поставку: переустановите систему из свежего "
                        "архива." % config.sample_file())
    else:
        lines.append("OK  Образец метрик прочитан: %d постов (%s)."
                     % (len(posts), ", ".join(sorted({p.get("platform", "?")
                                                      for p in posts}))))

    # 4. Главная проверка: сквозной прогон — нормализация → запись → рекомендации.
    demo_ok = False
    try:
        store = model.empty_store()
        for post in posts:
            res = providers.fetch_metrics(post, live=False)
            if res.get("ok"):
                rec = model.new_record(post, res["metrics"],
                                       res.get("source", "sample"))
                errs = model.validate(rec)
                if errs:
                    problems.append("Пост %s не прошёл схему: %s"
                                    % (rec["post_id"], "; ".join(errs)))
                else:
                    model.upsert(store, rec)
        blocks = recommender.recommend_store(store, thr)
        n_recs = sum(len(b["recommendations"]) for b in blocks)
        demo_ok = bool(blocks) and n_recs > 0
    except Exception as ex:
        problems.append("Сквозной прогон упал: %s. Это внутренняя ошибка модуля — "
                        "советов не будет вовсе, покажи строку человеку." % ex)

    if demo_ok:
        lines.append("OK  Сквозной прогон: %d постов → %d рекомендаций. Движок "
                     "«метрика→совет» отрабатывает." % (len(blocks), n_recs))
        # покажем один живой пример совета — «замер, не вывод»
        for b in blocks:
            if b["recommendations"]:
                lines.append("    пример: [%s] %s"
                             % (b["post_id"], b["recommendations"][0]["advice"][:80]))
                break

    # 4б. «Умный» шаг советов — заявленная заглушка (см. манифест модуля).
    #     Реализовывать её здесь не надо, но молчать о ней доктор не имеет права:
    #     человек должен понимать, что советы даёт детерминированный движок
    #     правил, а не модель. Код возврата это НЕ поднимает: манифест ровно так
    #     модуль и описывает («LLM-хук recommender.llm_advice — заглушка, платный
    #     API не зовёт»), то есть заявленный главный путь на месте и полон.
    lines.append("i   Советы даёт движок правил «метрика→совет» — без платного ИИ "
                 "и без обращений в сеть. Это штатный режим, а не урезанный: "
                 "«умный» шаг (recommender.llm_advice) намеренно оставлен "
                 "заглушкой, чтобы ваши деньги на API не тратились.")

    # 5. Состояние хранилища (если уже наполняли).
    try:
        real = model.load_store()
    except (ValueError, OSError) as ex:
        problems.append("Файл хранилища метрик повреждён и не читается: %s. "
                        "Накопленные замеры прочитать нельзя — соберите заново: "
                        "python -m analytics.cli ingest." % ex)
        real = None
    if real is not None:
        if real.get("posts"):
            lines.append("OK  Хранилище на месте: %d постов %s."
                         % (len(real["posts"]), model.counts(real)))
        else:
            lines.append("i   Хранилище ещё пустое — это нормально. Наполни: "
                         "python -m analytics.cli ingest.")

    # 6. История замеров — она и даёт динамику, а не только срез «сейчас».
    hist = history.summary()
    if hist["measurements"] >= 2:
        lines.append("OK  История замеров: %d замеров по %d постам (%s … %s). "
                     "Динамика считается: python -m analytics.cli history."
                     % (hist["measurements"], hist["posts"], hist["first"],
                        hist["last"]))
    elif hist["measurements"] == 1:
        lines.append("i   История: пока один замер. Динамика появится со второго "
                     "снятия метрик — это честное «данных ещё мало».")
    else:
        lines.append("i   История замеров пуста. Она наполняется сама при каждом "
                     "ingest (файл %s)." % paths.history_path())

    return problems, limits, lines


def main():
    try:
        problems, limits, lines = check()
    except Exception as ex:  # никаких стектрейсов клиенту
        print("Проверка модуля аналитики не смогла отработать: %s" % ex)
        print("Это сбой самой проверки. Покажите строку выше человеку.")
        return 1

    print("=== Проверка аналитики контента (модуль analytics) ===")
    for line in lines:
        print(" ", line)
    if problems:
        print()
        print("Не работает — это чинить:")
        for p in problems:
            print("  - " + p)
        if limits:
            print()
            print("Кроме того, часть возможностей недоступна:")
            for lim in limits:
                print("  - " + lim)
        return 1
    if limits:
        print()
        print("Модуль работает, но в урезанном режиме — вот чего не хватает:")
        for lim in limits:
            print("  - " + lim)
        print()
        print("Это не поломка и не срочно: отчёты и советы вы получите уже "
              "сейчас. Как заполните недостающее — цифры станут вашими.")
        return 2
    print()
    print("Модуль готов. Сними метрики (python -m analytics.cli ingest) и получи "
          "советы (recommend). Ничего не публикует. Живой сбор — через Upload-Post "
          "(тот же сервис, что публикует), только при наличии ключа; без ключа, без "
          "сети и при ошибке API честно откатывается на кэш/образец и не падает.")
    print("Что можно посмотреть БЕЗ сети и без трат: "
          "python -m analytics.cli status (состояние живого сбора), "
          "probe (сухой прогон: какие посты и сколько запросов), "
          "history (динамика между замерами).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
