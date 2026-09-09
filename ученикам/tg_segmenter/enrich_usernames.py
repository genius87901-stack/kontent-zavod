#!/usr/bin/env python3
"""Приклеивает @username к контактам базы (по телефону, затем по имени)
из tg_profiles.csv. Пишет/дополняет usernames.json {chat_id: '@username'} —
его подхватит build_xlsx и добавит колонку «Аккаунт» со ссылкой t.me."""
import csv, json, os, re

def norm(s): return re.sub(r'\s+',' ',(s or '').strip().lower())

prof=list(csv.DictReader(open('tg_profiles.csv', encoding='utf-8-sig')))
by_phone={}; by_name={}; dup=set()
for p in prof:
    un=(p.get('username') or '').strip()
    if not un: continue
    ph=re.sub(r'\D','', p.get('phone') or '')
    if ph: by_phone.setdefault(ph[-10:], un)
    nm=norm(p.get('first_name','')+' '+p.get('last_name',''))
    if nm:
        if nm in by_name and by_name[nm]!=un: dup.add(nm)
        by_name.setdefault(nm, un)

src='tg_contacts_full.csv' if os.path.exists('tg_contacts_full.csv') else 'tg_contacts_for_ai.csv'
base=list(csv.DictReader(open(src, encoding='utf-8-sig')))
out=json.load(open('usernames.json',encoding='utf-8')) if os.path.exists('usernames.json') else {}
m_ph=m_nm=0
for b in base:
    cid=b['chat_id']
    if cid in out: continue
    ph=re.sub(r'\D','', b.get('phone') or '')
    un=None
    if ph and ph[-10:] in by_phone: un=by_phone[ph[-10:]]; m_ph+=1
    else:
        nm=norm(b.get('name'))
        if nm and nm in by_name and nm not in dup: un=by_name[nm]; m_nm+=1
    if un: out[cid]='@'+un
json.dump(out, open('usernames.json','w',encoding='utf-8'), ensure_ascii=False, indent=0)
print(f'@username приклеено: по телефону {m_ph}, по имени {m_nm}, всего {len(out)}')
