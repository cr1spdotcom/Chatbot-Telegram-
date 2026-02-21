# Telegram Water Filtration Monitoring Bot

A Telegram bot that monitors water filtration sensor data from a CSV file. It shows status, readings, AI diagnosis (Gemini), trend analysis, and reports. When status is **Critical**, it sends alerts and can run AI-powered diagnostics.

---

## What You Need

- **Python 3.10 or higher**
- A **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))
- (Optional) **Gemini API key** – for AI diagnosis when status is Critical

---

## Requirements (packages)

All dependencies are in `requirements.txt`. Install them with:

```bash
pip install -r requirements.txt
```

| Package | Purpose |
|--------|---------|
| `python-telegram-bot==20.7` | Telegram Bot API |
| `pandas>=2.0` | Read/process CSV sensor data |
| `google-generativeai` | Gemini AI for Diagnose when status is Critical |

---

## Installation (Terminal)

### 1. Open a terminal in the project folder

```bash
cd path/to/Chatbot - THESIS
```

*(Replace `path/to/Chatbot - THESIS` with the actual folder path.)*

### 2. Create a virtual environment (recommended)

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

This installs:

- `python-telegram-bot==20.7` – Telegram Bot API
- `pandas>=2.0` – CSV and data handling
- `google-generativeai` – Gemini AI for diagnosis

### 4. (Optional) Install one by one

If you prefer to install packages separately:

```bash
pip install python-telegram-bot==20.7
pip install pandas>=2.0
pip install google-generativeai
```

---

## Setup Before Running

### 1. Bot token

1. In Telegram, open [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the steps to create a bot.
3. Copy the **Bot Token** BotFather gives you.
4. In `bot.py`, find the line with `BOT_TOKEN = "..."` and replace the value with your token.

### 2. (Optional) Gemini API key

- Used for **Diagnose** when status is **Critical**.
- Get a key from [Google AI Studio](https://aistudio.google.com/) (or Gemini API).
- In `bot.py`, find `GEMINI_API_KEY` and set your key there, or set the environment variable `GEMINI_API_KEY` before running the bot.

### 3. Data file: `data.csv`

The bot reads sensor data from `data.csv` in the same folder as `bot.py`.

Required columns:

| Column        | Description                |
|---------------|----------------------------|
| `timestamp`   | Date and time (e.g. `2026-01-21 14:00`) |
| `tds_before`  | TDS before filtration (ppm) |
| `ph_before`   | pH before filtration       |
| `temp_before` | Temperature before (°C)    |
| `tds_after`   | TDS after filtration (ppm)  |
| `ph_after`    | pH after filtration         |
| `temp_after`  | Temperature after (°C)      |

A sample `data.csv` is already in the project. You can edit it or replace it with your own data.

---

## Run the Bot

With the virtual environment activated (if you use one):

```bash
python bot.py
```

You should see something like:

```
Starting bot...
Application created.
Bot is starting... (Press Ctrl+C to stop)
```

Then open your bot in Telegram and send `/start`.

---

## Bot Commands / Menu

- **Current Status** – Latest sensor status (TDS, pH, Temp, Normal/Warning/Critical).
- **Readings** – Before/after filtration comparison.
- **Diagnose** – AI diagnosis (Gemini when Critical; rule-based otherwise).
- **Trend** – Trend over last 7 readings.
- **Daily Report** – Summary for today.
- **Weekly Report** – Summary for last 7 days.
- **Monthly Report** – Summary for last 30 days.
- **Help** – List of commands.

When status is **Critical**, the bot sends an alert to everyone who has used `/start`. Alert check runs every 5 seconds; repeat alerts are limited to once every 2 minutes.

---

## Project Files

| File              | Purpose                          |
|-------------------|----------------------------------|
| `bot.py`          | Main bot code                    |
| `data.csv`        | Sensor data (timestamp, TDS, pH, temp before/after) |
| `requirements.txt`| Python dependencies              |
| `README.md`       | This file                        |

---

## Troubleshooting

- **“No module named …”**  
  Run `pip install -r requirements.txt` again (with the same Python/venv you use for `python bot.py`).

- **Bot doesn’t reply**  
  Check that `BOT_TOKEN` in `bot.py` is correct and that the bot is not stopped in BotFather.

- **Diagnose / Gemini errors**  
  Set a valid `GEMINI_API_KEY` in `bot.py` or in the environment. If you don’t need AI, the bot still works and uses rule-based messages when Gemini fails.

- **“No data available”**  
  Ensure `data.csv` exists in the same folder as `bot.py` and has the columns listed above with at least one data row.
