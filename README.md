# Instagram Elite Telegram Bot

Telegram bot that scans Instagram followers / following / suggested accounts with full AI vision analysis, then presents results as swipeable profile cards.

## Features

- **Elite scan** — for every profile: full account data (`/v2/user/by/id`), country & registration date (`/v1/user/about`), up to 4 recent post photos (`/gql/user/medias`)
- **AI vision** — GPT-4.1-mini analyses profile pic + posts: age, hair colour, eye colour, figure type, interests, languages, gender
- **Swipe UI** — ❤️ Like / ⏭ Skip buttons on every card
- **Liked list** — `/liked` shows all liked profiles with Instagram links
- **Auto-skip** — verified, private, and very popular accounts (>10 000 followers) are shown but flagged

## Setup

### 1. Clone / copy this folder

```bash
cd "Insta_ai_telegram"
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create `.env`

```bash
cp .env.example .env
```

Fill in the three values:

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OPENAI_API_KEY` | platform.openai.com |
| `HIKER_API_TOKEN` | hikerapi.com |

### 3. Run

```bash
python bot.py
```

## Bot commands

| Command | Action |
|---|---|
| `/start` | Welcome message, ask for Instagram username |
| `/new` | Start a new scan (keeps liked list) |
| `/liked` | Show all liked profiles |
| `/cancel` | Cancel current operation |

## Flow

1. Send any Instagram username or profile URL
2. Choose **Following / Followers / Suggested**
3. Choose how many profiles to scan (10 / 25 / 50 / 100)
4. Bot scans in the background (~1–3 min for 25 profiles)
5. Profile cards arrive one by one — tap ❤️ or ⏭
6. When done, a summary of liked profiles is shown

## Notes

- Scanning 25 profiles makes ~75–100 HikerAPI calls and ~20–25 OpenAI vision calls
- The `skip_followers` threshold (default 10 000) prevents scanning large influencer accounts
- Profile photos are downloaded and sent directly to Telegram — no data URI conversion needed
