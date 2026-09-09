#!/usr/bin/env python3
"""Читает JSON-батч [[chat_id,segment,fit,priority,note],...] из stdin,
считает балл, дозаписывает в scored.csv. Валидация ID до записи."""
import csv, os, sys, json

marks = json.load(sys.stdin)
cand = {x['chat_id']: x for x in
        csv.DictReader(open('queue_candidates.csv', encoding='utf-8'))}

def score(x, fit, prio):
    s = {'hot':35,'warm':25,'cold':12}.get(x['warmth'], 0)
    s += 25 if fit in ('multi','both') else (0 if fit == 'none' else 20)
    s += 15 if prio == 1 else (5 if prio == 2 else 0)
    if fit != 'none': s += 12
    try:
        if int(x['msgs_them']) > int(x['msgs_me']): s += 6
    except Exception: pass
    if (x.get('phone') or '').strip(): s += 4
    return s

out = 'scored.csv'
cols = ['chat_id','name','warmth','msgs_them','msgs_me','days_silent',
        'segment','fit','priority','score','note']
missing = [m[0] for m in marks if m[0] not in cand]
if missing: raise SystemExit(f'НЕИЗВЕСТНЫЕ ID, ничего не записано: {missing}')
done = set()
if os.path.exists(out):
    done = {r['chat_id'] for r in csv.DictReader(open(out, encoding='utf-8'))}
dup = [m[0] for m in marks if m[0] in done]
if dup: raise SystemExit(f'УЖЕ РАЗМЕЧЕНЫ, ничего не записано: {dup}')

new = not os.path.exists(out)
with open(out,'a',encoding='utf-8',newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    if new: w.writeheader()
    for cid, seg, fit, prio, note in marks:
        x = cand[cid]
        w.writerow({'chat_id':cid,'name':x['name'],'warmth':x['warmth'],
                    'msgs_them':x['msgs_them'],'msgs_me':x['msgs_me'],
                    'days_silent':x['days_silent'],'segment':seg,'fit':fit,
                    'priority':prio,'score':score(x,fit,prio),'note':note})
print(f'записано {len(marks)}, всего:',
      sum(1 for _ in open(out, encoding='utf-8'))-1)
