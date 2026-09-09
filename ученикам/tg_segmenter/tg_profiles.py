#!/usr/bin/env python3
"""Собирает username + bio по всем диалогам и контактам через Telethon.
Нужны СВОИ ключи с https://my.telegram.org/apps :
    export TG_API_ID=...   TG_API_HASH=...
    python3 tg_profiles.py --sleep 1.5
Только читает: ничего не отправляет и никуда не вступает. Пишет tg_profiles.csv.
Прерывать/запускать заново можно — собранные id пропускаются. При FLOOD_WAIT
скрипт сам засыпает — не убивай процесс."""
import argparse, asyncio, csv, os, sys
from telethon import TelegramClient, functions, errors

F=["user_id","username","first_name","last_name","phone","bio",
   "is_bot","is_deleted","is_contact","has_dialog"]

async def run(a):
    client=TelegramClient("tg_session", int(os.environ["TG_API_ID"]),
                          os.environ["TG_API_HASH"])
    await client.start()   # первый раз спросит номер и код из Telegram
    done=set()
    exists=os.path.exists(a.out)
    if exists:
        for r in csv.DictReader(open(a.out, encoding="utf-8-sig")):
            done.add(r["user_id"])
        print("уже собрано:", len(done))
    dialog_ids=set()
    async for d in client.iter_dialogs():
        if d.is_user: dialog_ids.add(str(d.entity.id))
    print("личных диалогов:", len(dialog_ids))
    res=await client(functions.contacts.GetContactsRequest(hash=0))
    users={str(u.id):u for u in res.users}
    print("контактов в книге:", len(users))
    async for d in client.iter_dialogs():
        if d.is_user and str(d.entity.id) not in users:
            users[str(d.entity.id)]=d.entity
    todo=[uid for uid in users if uid not in done]
    print("к обработке:", len(todo))
    f=open(a.out,"a",encoding="utf-8-sig",newline="")
    w=csv.DictWriter(f, fieldnames=F)
    if not exists: w.writeheader()
    for i,uid in enumerate(todo,1):
        u=users[uid]; bio=""
        try:
            full=await client(functions.users.GetFullUserRequest(u))
            bio=(full.full_user.about or "").replace("\n"," ")
        except errors.FloodWaitError as e:
            print("FLOOD_WAIT", e.seconds, "с — жду", file=sys.stderr)
            await asyncio.sleep(e.seconds+5); continue
        except Exception as e:
            print(uid, e, file=sys.stderr)
        w.writerow({"user_id":uid,"username":getattr(u,"username","") or "",
            "first_name":getattr(u,"first_name","") or "",
            "last_name":getattr(u,"last_name","") or "",
            "phone":getattr(u,"phone","") or "","bio":bio,
            "is_bot":int(bool(getattr(u,"bot",False))),
            "is_deleted":int(bool(getattr(u,"deleted",False))),
            "is_contact":int(bool(getattr(u,"contact",False))),
            "has_dialog":int(uid in dialog_ids)})
        f.flush()
        if i%50==0: print(i,"/",len(todo))
        await asyncio.sleep(a.sleep)
    f.close(); print("готово ->", a.out)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--out", default="tg_profiles.csv")
    ap.add_argument("--sleep", type=float, default=1.5, help="пауза, сек (не <1.0)")
    asyncio.run(run(ap.parse_args()))
