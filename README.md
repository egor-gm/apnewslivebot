# AP News Live Bot

This simple bot monitors the AP News website for Live Blog posts and relays them to a Telegram channel.

## Setup

1. **Install dependencies** using Python 3.8 or newer:

```bash
pip install -r requirements.txt
```

2. **Required environment variables**
   - `TELEGRAM_BOT_TOKEN` – token of your Telegram bot.
   - `TELEGRAM_CHANNEL_ID` – target channel ID (e.g. `@mychannel` or numeric chat id).

3. **Optional interval settings** (all in seconds):
   - `CHECK_INTERVAL_SECONDS` – delay between cycles (default: 40).
   - `LONG_CHECK_INTERVAL_SECONDS` – used when no live topics were found for a long period (default: 300).
   - `NO_TOPICS_THRESHOLD_SECONDS` – how long to wait before switching to the long interval (default: 3600).

## Running the bot

After setting the required environment variables, run:

```bash
python apnewslivebot.py
```

The script will keep running and posting updates to the Telegram channel.

## Running tests

Install the requirements as above and execute:

```bash
pytest
```

This will run the unit tests under the `tests/` directory.
