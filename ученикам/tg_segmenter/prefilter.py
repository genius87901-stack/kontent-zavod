#!/usr/bin/env python3
"""Дешёвая фильтрация без модели: мусор, короткие, отбор верхних N в очередь."""
import argparse, csv, json, os, re

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--infile', default='tg_contacts_for_ai.csv')
    ap.add_argument('--top', type=int, default=600)
    ap.add_argument('--minlen', type=int, default=40)
    a = ap.parse_args()

    kw = None
    if os.path.exists('keywords.txt'):
        words = [w.strip().lower() for w in open('keywords.txt',encoding='utf-8')
                 if w.strip()]
        if words:
            kw = re.compile('|'.join(re.escape(w) for w in words), re.I)

    # Описания профилей (bio), приклеенные enrich_bio.py. Их может не быть —
    # шаг необязательный, требует ключей Telegram и входа самого пользователя.
    bios = {}
    if os.path.exists('bio.json'):
        try:
            bios = json.load(open('bio.json', encoding='utf-8'))
        except Exception:
            bios = {}

    def score(x):
        s = {'hot':35,'warm':25,'cold':12}.get(x['warmth'],0)
        txt = x['opening']+' '+x['their_last']+' '+x['my_last']
        if kw and kw.search(txt): s += 12
        # Совпадение по bio весит БОЛЬШЕ, чем по тексту переписки, и это не
        # произвол. В переписке ключевое слово может выскочить случайно —
        # человек упомянул чужой блог, переслал рекламу. В bio человек пишет
        # про СЕБЯ и по своей воле: «продюсер», «монтирую рилсы». Это прямое
        # самоопределение, а не совпадение по касательной.
        bio = (bios.get(x['chat_id']) or '').strip()
        if bio:
            x['bio'] = bio
            if kw and kw.search(bio): s += 20
        try:
            if int(x['msgs_them']) > int(x['msgs_me']): s += 6
        except Exception: pass
        return s

    rows = list(csv.DictReader(open(a.infile, encoding='utf-8-sig')))
    cand = []
    for x in rows:
        if x.get('maybe_junk') == '1': continue
        if len((x['opening']+x['their_last']+x['my_last']).strip()) < a.minlen:
            continue
        x['_s'] = score(x); cand.append(x)
    cand.sort(key=lambda x: x['_s'], reverse=True)

    cols = [c for c in rows[0].keys() if not c.startswith('_')]
    # bio едет отдельной колонкой, а не подмешивается в opening: при разметке
    # важно видеть, что это САМООПИСАНИЕ профиля, а не фраза из диалога.
    if bios and 'bio' not in cols:
        cols.append('bio')
    with open('queue_candidates.csv','w',encoding='utf-8',newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); [w.writerow({k:c.get(k,'') for k in cols}) for c in cand]

    from collections import Counter
    top = cand[:a.top]
    print(f'кандидатов: {len(cand)} | первый срез: {len(top)}')
    print('warmth в срезе:', dict(Counter(c['warmth'] for c in top)))
    if bios:
        with_bio = sum(1 for c in top if (c.get('bio') or '').strip())
        by_kw = sum(1 for c in top
                    if kw and kw.search(c.get('bio') or ''))
        print(f'bio: есть у {with_bio} из среза, из них по ключевым словам {by_kw}')
    else:
        print('bio: не собрано (шаг необязательный — enrich_bio.py после tg_profiles.py)')
    print('-> queue_candidates.csv (полный пул, отсортирован по баллу)')
    print(f'размечай батчами по 50, начиная сверху; первый срез = {a.top} строк')

if __name__ == '__main__':
    main()
