# How to export all your Telegram chats (EN)

You need **Telegram Desktop** — the mobile app cannot do a full export.

1. Install Telegram Desktop from the official page: **https://desktop.telegram.org**
   (Windows / macOS / Linux). Sign in to your account.
2. Menu → **Settings** → **Advanced**.
3. Scroll down to **Export Telegram data** and click it.
4. In the export window, **What to include**:
   - keep **Personal chats** enabled;
   - UNCHECK all media: Photos, Video, Voice messages, Video messages,
     Stickers, GIFs, Files — they are useless for segmentation and bloat the
     export. Keep text only.
   - (optional) enable **Contacts list** to also pull phone numbers.
5. **Location and format**:
   - **Format = HTML** (important — not JSON);
   - choose a folder to save into.
6. Click **Export** and wait for it to finish (minutes to hours depending on
   size). Telegram may ask you to wait 24h for security — that is normal, just
   wait it out.
7. A dated export folder appears (it contains **lists/** and **chats/**
   subfolders). Send Claude the full path to that folder.

---
## (Optional) Get api_id / api_hash — for @username and "group B"

Needed only if you want the "Account" column and finding forgotten contacts by bio.

1. Open **https://my.telegram.org** → enter your phone → the code arrives
   **inside Telegram** (not by SMS), enter it.
2. Go to **API development tools**.
   - If **App api_id** and **App api_hash** are already shown — the app exists,
     just copy them.
   - To create: **App title** = `parser`, **Short name** = `parser01`
     (5–32 chars, latin only, no spaces), **Platform** = Desktop, leave URL
     empty → **Create application**.
3. Common bug: the form shows **ERROR** even though the app was created — just
   **refresh** and reopen the section, the keys will be there. If not — switch
   language to English, try Chrome incognito without extensions, toggle VPN
   on/off, or use mobile internet.
4. Copy `api_id` (number) and `api_hash` (string) — tg_profiles.py will ask for them.
