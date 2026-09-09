#!/usr/bin/env python3
"""Сборка Excel из scored.csv: контакты сегментами по дням + оформление."""
import csv, json, os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

PER_DAY = 50   # контактов в одном дне (сегмент = один день)

rows = list(csv.DictReader(open('scored.csv', encoding='utf-8')))
for r in rows:
    r['score']=int(r['score']); r['priority']=int(r['priority'])
# готовые первые сообщения (по chat_id); messages.json — накопительный
msgs = {}
for _fn in ('messages.json','messages_block1.json'):
    if os.path.exists(_fn):
        msgs.update(json.load(open(_fn, encoding='utf-8')))

# @username по контактам (из Telethon-профиля), опционально
usernames = {}
if os.path.exists('usernames.json'):
    usernames = json.load(open('usernames.json', encoding='utf-8'))

# русские названия офферов: файл offers_ru.txt строками  ярлык=Название
OFFER_RU={'multi':'несколько','both':'несколько','none':'—'}
if os.path.exists('offers_ru.txt'):
    for _l in open('offers_ru.txt', encoding='utf-8'):
        if '=' in _l:
            _k,_v=_l.split('=',1); OFFER_RU[_k.strip().lower()]=_v.strip()
def offer_ru(f):
    f=(f or '').lower(); return OFFER_RU.get(f, f)

# приоритетная услуга: её аудитория идёт в очередь первой (дни начинаются с неё)
PRIORITY_OFFER=''
if os.path.exists('priority_offer.txt'):
    PRIORITY_OFFER=open('priority_offer.txt',encoding='utf-8').read().strip().lower()

send=[r for r in rows if r['priority'] in (1,2) and r['fit']!='none']
rest=[r for r in rows if not (r['priority'] in (1,2) and r['fit']!='none')]
def _rank(r):
    f=(r['fit'] or '').lower()
    first=0 if (PRIORITY_OFFER and (f==PRIORITY_OFFER or f in ('multi','both'))) else 1
    return (first, -r['score'], r['priority'])
send.sort(key=_rank)
rest.sort(key=lambda r:-r['score'])

HAS_ACC=bool(usernames)
H=['Имя']+(['Аккаунт'] if HAS_ACC else [])+['Сегмент','Оффер','Приоритет',
   'Контекст разговора','Пример первого сообщения']
N=len(H)
_W={'Имя':26,'Аккаунт':18,'Сегмент':24,'Оффер':16,'Приоритет':11,
    'Контекст разговора':46,'Пример первого сообщения':60}
WID=[_W[h] for h in H]
PRIO=H.index('Приоритет')+1
WRAP=[H.index('Контекст разговора')+1, H.index('Пример первого сообщения')+1]

# спокойная палитра для чередования дней
DAY=['D6E4F0','E2EFDA','EAE3F2','FCE9DD','D9EEF2','FFF4D6']
HEADER='44546A'; DIVGREY='D9D9D9'
FILL1='EDF5EC'; FILL3='F5F5F5'              # мягкая заливка строки по приоритету
PC1='C6EFCE'; PC2='DDEBF7'; PC3='E6E6E6'    # цвет ячейки «Приоритет»
thin=Side(style='thin', color='DADADA')
BORDER=Border(left=thin,right=thin,top=thin,bottom=thin)

wb=Workbook(); ws=wb.active; ws.title='Очередь'
for i,w in enumerate(WID,1): ws.column_dimensions[get_column_letter(i)].width=w

ws.append(H)
for c in range(1,N+1):
    cell=ws.cell(1,c)
    cell.fill=PatternFill('solid',fgColor=HEADER)
    cell.font=Font(color='FFFFFF',bold=True)
    cell.alignment=Alignment(vertical='center',horizontal='center',wrap_text=True)
    cell.border=BORDER
ws.row_dimensions[1].height=24
ws.freeze_panes='A2'

rownum=1

def divider(text, color):
    global rownum
    rownum+=1
    ws.merge_cells(start_row=rownum,start_column=1,end_row=rownum,end_column=N)
    c=ws.cell(rownum,1,text)
    c.fill=PatternFill('solid',fgColor=color)
    c.font=Font(bold=True,size=12,color='2A2A2A')
    c.alignment=Alignment(vertical='center',horizontal='left',indent=1)
    ws.row_dimensions[rownum].height=22

def _val(h, r):
    if h=='Имя': return r['name']
    if h=='Аккаунт': return usernames.get(r['chat_id'],'')
    if h=='Сегмент': return r['segment']
    if h=='Оффер': return offer_ru(r['fit'])
    if h=='Приоритет': return r['priority']
    if h=='Контекст разговора': return r['note']
    return msgs.get(r['chat_id'],'')          # Пример первого сообщения

def datarow(r):
    global rownum
    rownum+=1; p=r['priority']
    for c,h in enumerate(H,1):
        cell=ws.cell(rownum,c,_val(h,r)); cell.border=BORDER
        cell.alignment=Alignment(vertical='top', wrap_text=(c in WRAP),
            horizontal=('center' if c==PRIO else 'left'))
        if h=='Аккаунт' and cell.value:
            u=str(cell.value).lstrip('@')
            cell.value='@'+u; cell.hyperlink=f'https://t.me/{u}'
            cell.font=Font(color='0563C1', underline='single')
    rf = FILL1 if p==1 else (FILL3 if p==3 else None)
    if rf:
        for c in range(1,N+1): ws.cell(rownum,c).fill=PatternFill('solid',fgColor=rf)
    pc=ws.cell(rownum,PRIO)
    pc.fill=PatternFill('solid',fgColor=(PC1 if p==1 else (PC2 if p==2 else PC3)))
    pc.font=Font(bold=True)

ndays=(len(send)+PER_DAY-1)//PER_DAY if send else 0
for d in range(ndays):
    grp=send[d*PER_DAY:(d+1)*PER_DAY]
    divider(f"\U0001F4C5  Контакты на день {d+1}   ·   {len(grp)} чел.",
            DAY[d%len(DAY)])
    for r in grp: datarow(r)
if rest:
    divider(f"\U0001F515  Не писать   ·   низкий приоритет   ·   {len(rest)} чел.",
            DIVGREY)
    for r in rest: datarow(r)

# ---- Сводка ----
sm=wb.create_sheet('Сводка')
sm['A1']='Показатель'; sm['B1']='Значение'
sm['A1'].font=Font(bold=True); sm['B1'].font=Font(bold=True)
data=[('Всего к отправке', len(send)),
      ('Дней (по %d)'%PER_DAY, ndays),
      ('Priority 1 (первыми)', sum(1 for r in send if r['priority']==1)),
      ('Priority 2', sum(1 for r in send if r['priority']==2)),
      ('Не писать', len(rest))]
for i,(k,v) in enumerate(data,2): sm.cell(i,1,k); sm.cell(i,2,v)
sm.column_dimensions['A'].width=26; sm.column_dimensions['B'].width=14

# ---- Инструкция ----
ins=wb.create_sheet('Инструкция')
txt=['ПРАВИЛА РАССЫЛКИ — отправляете вы вручную из своего клиента','',
 '1. Максимум 50 сообщений в день, 4-5 подходов.',
 '2. В первом сообщении: без ссылок, прайсов, вложений.',
 '3. Первые 2 предложения уникальны у каждого (иначе спам-флаг).',
 '4. Первое сообщение не продаёт — возобновляет контакт и получает ответ.',
 '5. Опирайтесь на «Контекст разговора». Не выдумывайте факты.',
 '6. Заканчивайте вопросом, на который легко ответить одной строкой.',
 '7. На ответы реагируйте в тот же день. Никакой автоотправки.','',
 'Контакты разбиты по дням — идите сверху вниз, день за днём.','',
 'ЦВЕТА:',
 '  • цветная полоса = начало нового дня;',
 '  • зелёная строка = Priority 1 (писать первыми);',
 '  • серая строка = Priority 3 (не писать);',
 '  • ячейка «Приоритет»: зелёная=1, голубая=2, серая=3.']
for i,t in enumerate(txt,1):
    ins.cell(i,1,t)
    if i==1 or t=='ЦВЕТА:': ins.cell(i,1).font=Font(bold=True,size=12)
ins.column_dimensions['A'].width=95

wb.save('Очередь.xlsx')
print(f'OK -> Очередь.xlsx | к отправке: {len(send)} | дней: {ndays} '
      f'| не писать: {len(rest)}')
