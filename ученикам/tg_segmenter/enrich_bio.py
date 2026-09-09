#!/usr/bin/env python3
"""Приклеивает описание профиля (bio) к контактам, с которыми ПЕРЕПИСКА ЕСТЬ.

ЗАЧЕМ ОТДЕЛЬНЫЙ ШАГ. tg_profiles.py собирает bio по ВСЕМ пользователям, но
использовался он до сих пор только в build_groupB.py — а тот берёт ровно тех,
у кого диалога НЕТ (`has_dialog != 1`). То есть у основной базы, где людей
тысячи, bio просто выбрасывалось: ни в отбор, ни в разметку оно не попадало.

Почему это дорого. Bio — единственное место, где человек САМ пишет, чем он
занимается: «продюсер», «наставник», «монтирую рилсы». В переписке это
всплывает не всегда, особенно если общались о постороннем. Без bio такой
контакт уходит в «непонятно» и получает низкий приоритет — то есть теряется
целевой человек, с которым уже есть тёплый контакт. Это ровно та ошибка,
которую дороже всего не заметить: она молчаливая.

Что делает: сопоставляет профили с базой ПО ТЕЛЕФОНУ, затем ПО ИМЕНИ (та же
логика, что в enrich_usernames.py), и пишет bio.json {chat_id: bio}.
Дальше его подхватывает prefilter.py — bio участвует в поиске ключевых слов
и попадает в очередь отдельной колонкой, видимой при разметке.

Приватность: bio — публичное описание профиля, которое человек написал о себе
сам. Телефоны здесь не хранятся и никуда не идут, файл остаётся на машине.

Запуск (после tg_profiles.py):  python3 enrich_bio.py
"""
import csv
import json
import os
import re


def norm(s):
    return re.sub(r'\s+', ' ', (s or '').strip().lower())


def main():
    if not os.path.exists('tg_profiles.csv'):
        raise SystemExit('нет tg_profiles.csv — сначала соберите профили: '
                         'python3 tg_profiles.py')

    prof = list(csv.DictReader(open('tg_profiles.csv', encoding='utf-8-sig')))

    # Индексы профилей: по телефону (надёжно) и по имени (запасной путь).
    # Имя-дубль отбрасываем: приклеить чужое bio хуже, чем не приклеить своё.
    by_phone, by_name, dup = {}, {}, set()
    for p in prof:
        bio = (p.get('bio') or '').strip()
        if not bio:
            continue
        ph = re.sub(r'\D', '', p.get('phone') or '')
        if ph:
            by_phone.setdefault(ph[-10:], bio)
        nm = norm(p.get('first_name', '') + ' ' + p.get('last_name', ''))
        if nm:
            if nm in by_name and by_name[nm] != bio:
                dup.add(nm)
            by_name.setdefault(nm, bio)

    src = ('tg_contacts_full.csv' if os.path.exists('tg_contacts_full.csv')
           else 'tg_contacts_for_ai.csv')
    base = list(csv.DictReader(open(src, encoding='utf-8-sig')))

    out = {}
    if os.path.exists('bio.json'):
        out = json.load(open('bio.json', encoding='utf-8'))

    m_ph = m_nm = 0
    for b in base:
        cid = b['chat_id']
        if cid in out:
            continue
        ph = re.sub(r'\D', '', b.get('phone') or '')
        bio = None
        if ph and ph[-10:] in by_phone:
            bio = by_phone[ph[-10:]]
            m_ph += 1
        else:
            nm = norm(b.get('name'))
            if nm and nm in by_name and nm not in dup:
                bio = by_name[nm]
                m_nm += 1
        if bio:
            out[cid] = bio[:350]

    json.dump(out, open('bio.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=0)

    print(f'bio приклеено: по телефону {m_ph}, по имени {m_nm}, '
          f'всего {len(out)} из {len(base)} контактов базы')
    if src == 'tg_contacts_for_ai.csv':
        print('  ⚠️ tg_contacts_full.csv не найден — сопоставление шло только '
              'по имени. С ним совпадений было бы больше.')
    if dup:
        print(f'  пропущено из-за одинаковых имён у разных людей: {len(dup)}')
    print('дальше: python3 prefilter.py --top 600  (bio войдёт в отбор)')


if __name__ == '__main__':
    main()
