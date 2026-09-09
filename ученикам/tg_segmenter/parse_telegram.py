#!/usr/bin/env python3
"""Парсер HTML-выгрузки Telegram Desktop -> CSV для сегментации."""
import argparse, csv, html, os, re
from collections import deque, Counter
from datetime import datetime, date

RE_ENTRY = re.compile(
    r'<a class="entry block_link clearfix" href="\.\./chats/(chat_\d+)/messages\.html'
    r'.*?<div class="pull_right info details">\s*(.*?)\s*</div>'
    r'.*?<div class="name bold">\s*(.*?)\s*</div>'
    r'.*?<div class="details_entry details">\s*(.*?)\s*</div>', re.S)
RE_CONTACT = re.compile(
    r'<div class="entry clearfix">.*?<div class="name bold">\s*(.*?)\s*</div>'
    r'.*?<div class="details_entry details">\s*(.*?)\s*</div>', re.S)
RE_DATE = re.compile(r'<div class="pull_right date details" title="(\d{2}\.\d{2}\.\d{4})')
RE_FROM = re.compile(r'<div class="from_name">\s*(.*?)\s*</div>', re.S)
RE_TEXT = re.compile(r'<div class="text">\s*(.*?)\s*</div>', re.S)
RE_FWD = re.compile(r'class="[^"]*forwarded')
RE_TAG = re.compile(r'<[^>]+>')
BOT = re.compile(r'(bot$|_bot|бот$|notification|уведомл)', re.I)
SERVICE = re.compile(r'(доставка|яндекс|сбер|тинькофф|т-банк|альфа|озон|'
                     r'wildberries|avito|авито|такси|курьер|support|'
                     r'поддержк|госуслуг|deleted account)', re.I)

def clean(s, limit=400):
    s = RE_TAG.sub(' ', s.replace('<br>', ' '))
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()[:limit]

def parse_chat(folder, me):
    files = sorted([f for f in os.listdir(folder)
                    if re.fullmatch(r'messages\d*\.html', f)],
                   key=lambda x: int(re.sub(r'\D', '', x) or 1))
    mine = theirs = 0
    first = last = None
    opening, their_last, my_last = [], deque(maxlen=4), deque(maxlen=2)
    prev = None
    for fn in files:
        try:
            raw = open(os.path.join(folder, fn), encoding='utf-8').read()
        except Exception:
            continue
        for chunk in raw.split('<div class="message ')[1:]:
            if chunk[:80].startswith('service'):
                continue
            m = RE_DATE.search(chunk)
            if m:
                if first is None: first = m.group(1)
                last = m.group(1)
            fwd = RE_FWD.search(chunk)
            cut = fwd.start() if fwd else len(chunk)
            fm = RE_FROM.search(chunk, 0, cut)
            sender = clean(fm.group(1), 80) if fm else prev
            prev = sender or prev
            is_mine = bool(me) and (sender or '').startswith(me)
            txt = ' '.join(clean(t, 300) for t in RE_TEXT.findall(chunk))
            if is_mine:
                mine += 1
                if txt: my_last.append(txt)
            else:
                theirs += 1
                if txt: their_last.append(txt)
            if txt and len(opening) < 4:
                opening.append(('Я: ' if is_mine else 'ОН: ') + txt)
    return mine, theirs, first, last, opening, list(their_last), list(my_last)

def to_iso(d):
    try: return datetime.strptime(d, '%d.%m.%Y').date().isoformat()
    except Exception: return ''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir')
    ap.add_argument('--me', default='', help='Ваше имя как в исходящих сообщениях')
    ap.add_argument('--outdir', default='.')
    a = ap.parse_args()
    if not a.me:
        print('ВНИМАНИЕ: --me не задан, все сообщения будут считаться "их". '
              'Передайте ваше имя как в Telegram.')

    lists = os.path.join(a.export_dir, 'lists')
    chats_html = open(os.path.join(lists, 'chats.html'), encoding='utf-8').read()
    entries = RE_ENTRY.findall(chats_html)
    print(f'чатов в выгрузке: {len(entries)}')

    phones = {}
    cpath = os.path.join(lists, 'contacts.html')
    if os.path.exists(cpath):
        for nm, ph in RE_CONTACT.findall(open(cpath, encoding='utf-8').read()):
            nm = clean(nm, 120)
            if nm and nm != 'Deleted Account':
                phones.setdefault(nm.lower(), clean(ph, 30))
    print(f'контактов с телефоном: {len(phones)}')

    today = date.today()
    rows, skipped = [], 0
    for i, (folder, ctype, name, cnt) in enumerate(entries, 1):
        if ctype.strip().lower() != 'private':
            skipped += 1; continue
        name = clean(name, 120)
        path = os.path.join(a.export_dir, 'chats', folder)
        if not os.path.isdir(path): continue
        mine, theirs, first, last, opening, tl, ml = parse_chat(path, a.me)
        total = mine + theirs
        if total == 0: continue
        warmth = 'hot' if (mine and theirs and total >= 10) else \
                 ('warm' if (mine and theirs) else 'cold')
        last_iso = to_iso(last)
        silent = ''
        if last_iso:
            try: silent = (today - date.fromisoformat(last_iso)).days
            except Exception: silent = ''
        rows.append({
            'chat_id': folder, 'name': name, 'phone': phones.get(name.lower(), ''),
            'msgs_total': total, 'msgs_me': mine, 'msgs_them': theirs,
            'first_contact': to_iso(first), 'last_contact': last_iso,
            'days_silent': silent, 'warmth': warmth,
            'maybe_junk': int(bool(BOT.search(name) or SERVICE.search(name))),
            'opening': ' | '.join(opening)[:400],
            'their_last': ' | '.join(tl)[:400],
            'my_last': ' | '.join(ml)[:200],
            'segment': '', 'fit': '', 'status': '', 'note': ''})
        if i % 250 == 0: print(f'{i}/{len(entries)}')

    full = ['chat_id','name','phone','msgs_total','msgs_me','msgs_them',
            'first_contact','last_contact','days_silent','warmth','maybe_junk',
            'opening','their_last','my_last','segment','fit','status','note']
    ai = [f for f in full if f != 'phone']
    os.makedirs(a.outdir, exist_ok=True)
    for p, fields in ((os.path.join(a.outdir,'tg_contacts_full.csv'), full),
                      (os.path.join(a.outdir,'tg_contacts_for_ai.csv'), ai)):
        with open(p,'w',encoding='utf-8-sig',newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader(); [w.writerow(r) for r in rows]

    c = Counter(r['warmth'] for r in rows)
    print(f"\nличных диалогов с сообщениями: {len(rows)}")
    print(f"  hot: {c['hot']}  warm: {c['warm']}  cold: {c['cold']}")
    print(f"  ботов/сервисов помечено: {sum(r['maybe_junk'] for r in rows)}")
    print(f"файлы -> {os.path.abspath(a.outdir)}")

if __name__ == '__main__':
    main()
