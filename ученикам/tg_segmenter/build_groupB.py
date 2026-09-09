#!/usr/bin/env python3
"""Группа Б: люди БЕЗ переписки, отобранные по bio под ключевые слова.
Дописывает их в queue_candidates.csv (opening = BIO) — размечаются тем же
score_batch, что и основная база; их @username кладутся в usernames.json."""
import csv, json, os, re

if not os.path.exists('tg_profiles.csv'):
    raise SystemExit('нет tg_profiles.csv — сначала соберите профили: tg_profiles.py')
kw=None
if os.path.exists('keywords.txt'):
    words=[w.strip().lower() for w in open('keywords.txt',encoding='utf-8') if w.strip()]
    if words: kw=re.compile('|'.join(re.escape(w) for w in words), re.I)

COLS=['chat_id','name','msgs_total','msgs_me','msgs_them','first_contact',
      'last_contact','days_silent','warmth','maybe_junk','opening','their_last',
      'my_last','segment','fit','status','note']
have=set()
if os.path.exists('queue_candidates.csv'):
    rd=csv.DictReader(open('queue_candidates.csv', encoding='utf-8'))
    COLS=rd.fieldnames or COLS
    have={r['chat_id'] for r in rd}

un=json.load(open('usernames.json',encoding='utf-8')) if os.path.exists('usernames.json') else {}
prof=list(csv.DictReader(open('tg_profiles.csv', encoding='utf-8-sig')))
rowsB=[]
for p in prof:
    if p.get('has_dialog')=='1': continue          # уже в основной базе
    if p.get('is_bot')=='1' or p.get('is_deleted')=='1': continue
    bio=(p.get('bio') or '').strip()
    if not bio: continue
    if kw and not kw.search(bio): continue
    uid=p['user_id']
    if uid in have: continue
    name=(p.get('first_name','')+' '+p.get('last_name','')).strip() \
         or ('@'+(p.get('username') or uid))
    row={c:'' for c in COLS}
    row.update({'chat_id':uid,'name':name,'msgs_total':'0','msgs_me':'0',
        'msgs_them':'0','warmth':'cold','maybe_junk':'0',
        'opening':'BIO: '+bio[:350]})
    # Та же колонка, что у основной базы (её заполняет prefilter из bio.json):
    # у группы Б bio — вообще единственное, что про человека известно.
    if 'bio' in COLS:
        row['bio'] = bio[:350]
    rowsB.append(row)
    if p.get('username'): un[uid]='@'+p['username']

if rowsB:
    new=not os.path.exists('queue_candidates.csv')
    with open('queue_candidates.csv','a',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f, fieldnames=COLS)
        if new: w.writeheader()
        for r in rowsB: w.writerow(r)
    json.dump(un, open('usernames.json','w',encoding='utf-8'), ensure_ascii=False, indent=0)
print(f'группа Б добавлена в очередь: {len(rowsB)} чел. (по bio + ключевым словам)')
