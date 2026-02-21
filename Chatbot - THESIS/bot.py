import os
import sys
import csv
import time
import pandas as pd
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
import asyncio
import google.generativeai as genai

# Force unbuffered output so we see prints immediately
sys.stdout.reconfigure(line_buffering=True)

# Bot Token - Replace with your BotFather token
BOT_TOKEN = "8167856886:AAHtbzpVfJdQLj9guosKV5-nqg8axNz7d8s"
CSV_FILE = "data.csv"

# Gemini AI – used when Diagnose is clicked and status is Critical (google.generativeai)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AIzaSyDV7XEurfIi66-85Kwj4uWX5H1DeoJq8L4"
genai.configure(api_key=GEMINI_API_KEY)

# Thresholds for status determination
TDS_NORMAL_MAX = 300
TDS_WARNING_MAX = 500
PH_NORMAL_MIN = 6.5
PH_NORMAL_MAX = 7.5
TEMP_NORMAL_MIN = 25
TEMP_NORMAL_MAX = 30

# Alerts: users who get Critical notifications (chat ids)
ALERT_CHAT_IDS = set()
LAST_CRITICAL_ALERT_AT = None  # timestamp; cooldown between alerts
ALERT_COOLDOWN_SEC = 2 * 60   # 2 minutes between repeat alerts (avoids spam if status stays Critical)
ALERT_CHECK_INTERVAL_SEC = 5  # check status every 5 seconds (faster detection)

def get_latest_reading():
    """Get the most recent reading from CSV"""
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty:
            return None
        latest = df.iloc[-1]
        return {
            'timestamp': latest['timestamp'],
            'tds_before': float(latest['tds_before']),
            'ph_before': float(latest['ph_before']),
            'temp_before': float(latest['temp_before']),
            'tds_after': float(latest['tds_after']),
            'ph_after': float(latest['ph_after']),
            'temp_after': float(latest['temp_after'])
        }
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return None

def get_current_sensor_status():
    """Get current real-time sensor status (using latest reading as current)"""
    reading = get_latest_reading()
    if not reading:
        return None
    
    # Use 'after' values as current status
    tds = reading['tds_after']
    ph = reading['ph_after']
    temp = reading['temp_after']
    
    # Determine status
    status = "Normal"
    if tds > TDS_WARNING_MAX or ph < PH_NORMAL_MIN or ph > PH_NORMAL_MAX or temp < TEMP_NORMAL_MIN or temp > TEMP_NORMAL_MAX:
        status = "Critical"
    elif tds > TDS_NORMAL_MAX:
        status = "Warning"
    
    return {
        'timestamp': reading['timestamp'],
        'tds': tds,
        'ph': ph,
        'temp': temp,
        'status': status
    }

def determine_status(tds, ph, temp):
    """Determine status based on readings"""
    if tds > TDS_WARNING_MAX or ph < PH_NORMAL_MIN or ph > PH_NORMAL_MAX or temp < TEMP_NORMAL_MIN or temp > TEMP_NORMAL_MAX:
        return "Critical"
    elif tds > TDS_NORMAL_MAX:
        return "Warning"
    return "Normal"

async def get_gemini_recommendation(sensor_data: dict) -> dict | None:
    """Call Gemini API for AI diagnosis. Returns {issue, reason, recommendation}, or {raw: str}, or None on failure."""
    try:
        prompt = f"""You are a friendly water filtration AI expert. The system status is CRITICAL. Give a clear, helpful diagnosis like a conversational AI assistant.

Write in this exact format (each line starting with the label). Be specific and actionable:

Possible Issue: [What’s wrong in plain language]
Reason: [Why this is happening, using the readings below]
Recommendation: [What to do – use bullet points with • if there are several steps]

Current readings:
• TDS before: {sensor_data.get('tds_before')} ppm → after: {sensor_data.get('tds_after')} ppm
• pH before: {sensor_data.get('ph_before')} → after: {sensor_data.get('ph_after')}
• Temp before: {sensor_data.get('temp_before')}°C → after: {sensor_data.get('temp_after')}°C
• Filtration efficiency: {sensor_data.get('efficiency', 'N/A')}%
• TDS drop (vs prior): {sensor_data.get('tds_drop', 'N/A')}%

Thresholds: TDS normal ≤{TDS_NORMAL_MAX} ppm, warning ≤{TDS_WARNING_MAX} ppm; pH 6.5–7.5; temp 25–30°C.

Reply only with the three labeled lines. No extra greeting or footer."""

        loop = asyncio.get_event_loop()

        def _call():
            text = None
            for name in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro-latest", "gemini-1.5-flash", "gemini-1.5-pro"):
                try:
                    model = genai.GenerativeModel(name)
                    r = model.generate_content(prompt)
                    if r and getattr(r, "text", None):
                        text = str(r.text).strip()
                        break
                except Exception as e:
                    print(f"Gemini {name}: {e}")
                    continue
            if not text:
                return None
            # Parse "Possible Issue: ..." etc. (allow "Issue:" or "**Possible Issue**" as variants)
            issue = reason = recommendation = None
            for line in text.split("\n"):
                s = line.strip()
                if not s:
                    continue
                if s.lower().startswith("possible issue:") or s.lower().startswith("issue:"):
                    issue = (s.split(":", 1)[-1] if ":" in s else s).strip()
                elif s.lower().startswith("reason:"):
                    reason = (s.split(":", 1)[-1] if ":" in s else s).strip()
                elif s.lower().startswith("recommendation:"):
                    recommendation = (s.split(":", 1)[-1] if ":" in s else s).strip()
            if issue and reason and recommendation:
                return {"issue": issue, "reason": reason, "recommendation": recommendation}
            # If we got text but parsing failed, return it as raw so we can still show the AI reply
            return {"raw": text}

        return await loop.run_in_executor(None, _call)
    except Exception as e:
        print(f"get_gemini_recommendation: {e}")
        return None

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command - Current Sensor Status"""
    status = get_current_sensor_status()
    if not status:
        message = "No sensor data available."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(message)
        else:
            await update.message.reply_text(message)
        return
    
    try:
        dt = datetime.strptime(status['timestamp'], "%Y-%m-%d %H:%M")
        formatted_time = dt.strftime("%m-%d-%Y %I:%M %p").lower()
    except:
        formatted_time = status['timestamp']
    
    message = f"{formatted_time}\n\n"
    message += f"TDS: {status['tds']} ppm\n"
    message += f"pH: {status['ph']}\n"
    message += f"Temp: {status['temp']}°C\n"
    message += f"Status: {status['status']}"
    
    if update.callback_query:
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message)

async def readings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /readings command - Comparison Before and After Filtration"""
    reading = get_latest_reading()
    if not reading:
        message = "No readings available."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(message)
        else:
            await update.message.reply_text(message)
        return
    
    message = "Readings Before Filtration\n"
    message += f"TDS: {reading['tds_before']} ppm\n"
    message += f"pH: {reading['ph_before']}\n"
    message += f"Temp: {reading['temp_before']}°C\n\n"
    message += "Readings After Filtration\n"
    message += f"TDS: {reading['tds_after']} ppm\n"
    message += f"pH: {reading['ph_after']}\n"
    message += f"Temp: {reading['temp_after']}°C"
    
    if update.callback_query:
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message)

async def diagnose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /diagnose command - AI Diagnostics. When status is Critical, Gemini API is used."""
    try:
        df = pd.read_csv(CSV_FILE)
        if len(df) < 2:
            message = "Insufficient data for diagnosis. Need at least 2 readings."
            if update.callback_query:
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return

        last = df.iloc[-1]
        prev = df.iloc[-2]

        tds_drop = ((float(prev['tds_before']) - float(last['tds_after'])) / float(prev['tds_before'])) * 100
        efficiency = ((float(last['tds_before']) - float(last['tds_after'])) / float(last['tds_before'])) * 100

        current_status = determine_status(
            float(last['tds_after']),
            float(last['ph_after']),
            float(last['temp_after'])
        )

        issue = None
        reason = None
        recommendation = None

        if current_status == "Critical":
            if update.callback_query:
                status_msg = await update.callback_query.message.reply_text("🤖 AI is analyzing critical status...")
            else:
                status_msg = await update.message.reply_text("🤖 AI is analyzing critical status...")

            sensor_data = {
                "tds_before": float(last['tds_before']),
                "tds_after": float(last['tds_after']),
                "ph_before": float(last['ph_before']),
                "ph_after": float(last['ph_after']),
                "temp_before": float(last['temp_before']),
                "temp_after": float(last['temp_after']),
                "efficiency": round(efficiency, 1),
                "tds_drop": round(tds_drop, 1),
            }
            ai = await get_gemini_recommendation(sensor_data)

            try:
                await status_msg.delete()
            except Exception:
                pass

            if ai and ai.get("raw"):
                issue = None
                reason = None
                recommendation = None
                message = "🤖 AI diagnosis\n\n" + ai["raw"]
            elif ai and ai.get("issue") and ai.get("reason") and ai.get("recommendation"):
                issue = ai["issue"]
                reason = ai["reason"]
                recommendation = ai["recommendation"]
            else:
                tds_a, ph_a, temp_a = float(last['tds_after']), float(last['ph_after']), float(last['temp_after'])
                if tds_a > TDS_WARNING_MAX:
                    issue = "Critical TDS level detected"
                    reason = f"TDS after filtration ({tds_a:.0f} ppm) is above the safe limit of {TDS_WARNING_MAX} ppm."
                    recommendation = (
                        "• Inspect the filtration unit and pipes for blockages or leaks.\n"
                        "• Replace or clean filter media as recommended by the manufacturer.\n"
                        "• If the problem continues, consider a professional service check."
                    )
                elif ph_a < PH_NORMAL_MIN or ph_a > PH_NORMAL_MAX:
                    issue = "pH imbalance detected"
                    reason = f"pH ({ph_a}) is outside the normal range (6.5–7.5), which can affect water quality and equipment."
                    recommendation = (
                        "• Check and calibrate the pH adjustment system.\n"
                        "• Verify that filter media and chemicals are within their shelf life.\n"
                        "• Retest after adjustments and monitor over the next few cycles."
                    )
                else:
                    issue = "Critical system condition detected"
                    reason = f"One or more parameters (TDS {tds_a:.0f} ppm, pH {ph_a}, temp {temp_a}°C) are outside normal limits."
                    recommendation = (
                        "• Review each sensor and its wiring.\n"
                        "• Confirm calibration and that probes are clean and submerged.\n"
                        "• If values stay abnormal, schedule a full system check."
                    )

        else:
            if tds_drop < 15:
                issue = "Filter saturation detected"
                reason = f"TDS drop only {tds_drop:.1f}% from last cycle (expected >20%)"
                recommendation = "Replace activated carbon in 7 days"
            elif tds_drop < 20:
                issue = "Reduced filtration efficiency"
                reason = f"TDS reduction is {tds_drop:.1f}% (below optimal 20%+)"
                recommendation = "Monitor closely; consider filter replacement in 10–14 days"
            else:
                issue = "System operating normally"
                reason = f"Filtration efficiency is {tds_drop:.1f}%"
                recommendation = "Continue regular monitoring"

        if issue is not None and reason is not None and recommendation is not None:
            message = "🤖 AI diagnosis\n\n"
            message += f"Possible Issue: {issue}\n\n"
            message += f"Reason: {reason}\n\n"
            message += f"Recommendation: {recommendation}"

        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message, reply_markup=reply_markup)
    except Exception as e:
        error_msg = f"Error in diagnosis: {str(e)}"
        if update.callback_query:
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

async def trend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trend command - Trend Analysis"""
    try:
        df = pd.read_csv(CSV_FILE)
        if len(df) < 2:
            message = "Insufficient data for trend analysis."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        recent = df.tail(7)
        if len(recent) < 2:
            message = "Need at least 2 readings for trend analysis."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        first = recent.iloc[0]
        last = recent.iloc[-1]
        tds_first = float(first['tds_after'])
        tds_last = float(last['tds_after'])
        tds_trend = "↓" if tds_last < tds_first else "↑" if tds_last > tds_first else "→"
        ph_first = float(first['ph_after'])
        ph_last = float(last['ph_after'])
        ph_diff = abs(ph_last - ph_first)
        ph_trend = "stable" if ph_diff < 0.3 else ("↓" if ph_last < ph_first else "↑")
        temp_first = float(first['temp_after'])
        temp_last = float(last['temp_after'])
        temp_diff = abs(temp_last - temp_first)
        temp_trend = "stable" if temp_diff < 1 else ("↓" if temp_last < temp_first else "↑")
        efficiency_first = ((float(first['tds_before']) - float(first['tds_after'])) / float(first['tds_before'])) * 100
        efficiency_last = ((float(last['tds_before']) - float(last['tds_after'])) / float(last['tds_before'])) * 100
        efficiency_trend = "↑" if efficiency_last > efficiency_first else "↓" if efficiency_last < efficiency_first else "→"
        efficiency_change = abs(efficiency_last - efficiency_first)
        
        message = f"Last {len(recent)} readings:\n"
        message += f"TDS: {tds_trend} ({tds_first} → {tds_last})\n"
        message += f"pH: {ph_trend} ({ph_first} → {ph_last})\n"
        message += f"Temp: {temp_trend} ({temp_first} → {temp_last})\n\n"
        message += f"Filtration Efficiency {efficiency_trend} {efficiency_change:.0f}%"
        
        if update.callback_query:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message)
    except Exception as e:
        error_msg = f"Error in trend analysis: {str(e)}"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

async def report_day_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report_day command - Daily Summary"""
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty:
            message = "No data available."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        today = datetime.now().date()
        today_data = df[df['timestamp'].dt.date == today]
        if today_data.empty:
            message = "No data available for today."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        avg_tds = today_data['tds_after'].mean()
        avg_ph = today_data['ph_after'].mean()
        avg_temp = today_data['temp_after'].mean()
        efficiencies = []
        for _, row in today_data.iterrows():
            eff = ((row['tds_before'] - row['tds_after']) / row['tds_before']) * 100
            efficiencies.append(eff)
        best_efficiency = max(efficiencies) if efficiencies else 0
        avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 0
        message = f"Daily Summary ({today.strftime('%b %d, %Y')})\n"
        message += f"Avg TDS: {avg_tds:.0f} ppm\n"
        message += f"Avg pH: {avg_ph:.1f}\n"
        message += f"Avg Temp: {avg_temp:.1f}°C\n"
        message += f"Best Efficiency: {best_efficiency:.0f}%\n"
        message += f"Avg Efficiency: {avg_efficiency:.0f}%\n"
        message += f"Total Cycles: {len(today_data)}"
        
        if update.callback_query:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message)
    except Exception as e:
        error_msg = f"Error generating report: {str(e)}"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

async def report_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report_week command - Weekly Summary"""
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty:
            message = "No data available."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        week_ago = datetime.now() - timedelta(days=7)
        week_data = df[df['timestamp'] >= week_ago]
        if week_data.empty:
            message = "No data available for the last 7 days."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        start_date = week_data['timestamp'].min().strftime('%b %d')
        end_date = week_data['timestamp'].max().strftime('%b %d')
        avg_tds = week_data['tds_after'].mean()
        avg_ph = week_data['ph_after'].mean()
        avg_temp = week_data['temp_after'].mean()
        efficiencies = []
        for _, row in week_data.iterrows():
            eff = ((row['tds_before'] - row['tds_after']) / row['tds_before']) * 100
            efficiencies.append(eff)
        best_efficiency = max(efficiencies) if efficiencies else 0
        message = f"Weekly Summary ({start_date}–{end_date})\n"
        message += f"Avg TDS: {avg_tds:.0f} ppm\n"
        message += f"Avg pH: {avg_ph:.1f}\n"
        message += f"Avg Temp: {avg_temp:.1f}°C\n"
        message += f"Best Efficiency: {best_efficiency:.0f}%\n"
        message += f"Total Cycles: {len(week_data)}"
        
        if update.callback_query:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message)
    except Exception as e:
        error_msg = f"Error generating report: {str(e)}"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

async def report_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report_month command - Monthly Summary"""
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty:
            message = "No data available."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        month_ago = datetime.now() - timedelta(days=30)
        month_data = df[df['timestamp'] >= month_ago]
        if month_data.empty:
            message = "No data available for the last 30 days."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
            return
        
        start_date = month_data['timestamp'].min().strftime('%b %d')
        end_date = month_data['timestamp'].max().strftime('%b %d')
        avg_tds = month_data['tds_after'].mean()
        avg_ph = month_data['ph_after'].mean()
        avg_temp = month_data['temp_after'].mean()
        efficiencies = []
        for _, row in month_data.iterrows():
            eff = ((row['tds_before'] - row['tds_after']) / row['tds_before']) * 100
            efficiencies.append(eff)
        best_efficiency = max(efficiencies) if efficiencies else 0
        message = f"Monthly Summary ({start_date}–{end_date})\n"
        message += f"Avg TDS: {avg_tds:.0f} ppm\n"
        message += f"Avg pH: {avg_ph:.1f}\n"
        message += f"Avg Temp: {avg_temp:.1f}°C\n"
        message += f"Best Efficiency: {best_efficiency:.0f}%\n"
        message += f"Total Cycles: {len(month_data)}"
        
        if update.callback_query:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message)
    except Exception as e:
        error_msg = f"Error generating report: {str(e)}"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

def _main_menu_keyboard():
    """Main menu with Diagnose always shown."""
    return [
        [
            InlineKeyboardButton("📊 Current Status", callback_data="status"),
            InlineKeyboardButton("📈 Readings", callback_data="readings"),
        ],
        [
            InlineKeyboardButton("🔍 Diagnose", callback_data="diagnose"),
            InlineKeyboardButton("📉 Trend", callback_data="trend"),
        ],
        [
            InlineKeyboardButton("📅 Daily Report", callback_data="report_day"),
            InlineKeyboardButton("📆 Weekly Report", callback_data="report_week"),
        ],
        [
            InlineKeyboardButton("📋 Monthly Report", callback_data="report_month"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help"),
        ],
    ]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    ALERT_CHAT_IDS.add(update.message.chat.id)
    message = "🤖 Water Filtration Monitoring Bot\n\n"
    message += "Select an option from the buttons below:"
    reply_markup = InlineKeyboardMarkup(_main_menu_keyboard())
    await update.message.reply_text(message, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await start_command(update, context)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    ALERT_CHAT_IDS.add(query.message.chat.id)
    await query.answer()  # Acknowledge the callback
    
    callback_data = query.data
    
    if callback_data == "status":
        await status_command(update, context)
    elif callback_data == "readings":
        await readings_command(update, context)
    elif callback_data == "diagnose":
        await diagnose_command(update, context)
    elif callback_data == "trend":
        await trend_command(update, context)
    elif callback_data == "report_day":
        await report_day_command(update, context)
    elif callback_data == "report_week":
        await report_week_command(update, context)
    elif callback_data == "report_month":
        await report_month_command(update, context)
    elif callback_data == "help":
        message = "🤖 Water Filtration Monitoring Bot\n\n"
        message += "Available Commands:\n"
        message += "📊 Current Status - Real-time sensor readings\n"
        message += "📈 Readings - Before/After filtration comparison\n"
        message += "🔍 Diagnose - AI-powered diagnostics\n"
        message += "📉 Trend - Trend analysis (last 7 readings)\n"
        message += "📅 Daily Report - Today's summary\n"
        message += "📆 Weekly Report - Last 7 days summary\n"
        message += "📋 Monthly Report - Last 30 days summary\n\n"
        message += "You can also type commands like /status, /readings, etc."
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup)
    elif callback_data == "start":
        message = "🤖 Water Filtration Monitoring Bot\n\n"
        message += "Select an option from the buttons below:"
        reply_markup = InlineKeyboardMarkup(_main_menu_keyboard())
        await query.edit_message_text(message, reply_markup=reply_markup)

async def check_alerts(bot):
    """When status is Critical, send an alert to all subscribed chats. Cooldown 15 min."""
    global LAST_CRITICAL_ALERT_AT
    status = get_current_sensor_status()
    if not status:
        return
    if status["status"] != "Critical":
        LAST_CRITICAL_ALERT_AT = None
        return
    import time
    now = time.time()
    if LAST_CRITICAL_ALERT_AT is not None and (now - LAST_CRITICAL_ALERT_AT) < ALERT_COOLDOWN_SEC:
        return
    LAST_CRITICAL_ALERT_AT = now

    msg = (
        "⚠️ CRITICAL STATUS ALERT\n\n"
        f"Time: {status['timestamp']}\n"
        f"TDS: {status['tds']} ppm · pH: {status['ph']} · Temp: {status['temp']}°C\n\n"
        "Tap 🔍 Diagnose in the bot for AI analysis and recommendations."
    )
    for cid in list(ALERT_CHAT_IDS):
        try:
            await bot.send_message(chat_id=cid, text=msg)
        except Exception:
            pass

async def predictive_replacement(context: ContextTypes.DEFAULT_TYPE):
    """Calculate and send predictive replacement estimates"""
    try:
        df = pd.read_csv(CSV_FILE)
        if len(df) < 5:
            return
        
        # Calculate average efficiency decline
        recent = df.tail(10)
        efficiencies = []
        for _, row in recent.iterrows():
            eff = ((row['tds_before'] - row['tds_after']) / row['tds_before']) * 100
            efficiencies.append(eff)
        
        if len(efficiencies) < 5:
            return
        
        # Simple linear prediction
        avg_eff = sum(efficiencies) / len(efficiencies)
        if avg_eff < 20:
            days_remaining = max(1, int((20 - avg_eff) / 2))
            # In production, send to users
            # This is a placeholder
            pass
    except:
        pass

async def _alert_loop(app):
    """Background task: check status every ALERT_CHECK_INTERVAL_SEC and send Critical alerts."""
    while True:
        await asyncio.sleep(ALERT_CHECK_INTERVAL_SEC)
        try:
            await check_alerts(app.bot)
        except Exception as e:
            print(f"check_alerts: {e}", flush=True)

async def _post_init(app):
    # Register command menu (shows when user types / in Telegram)
    await app.bot.set_my_commands([
        BotCommand("start", "Get started with the water filtration bot"),
        BotCommand("help", "Show help and commands"),
        BotCommand("status", "Current sensor status"),
        BotCommand("readings", "Before/after filtration comparison"),
        BotCommand("diagnose", "AI diagnostics and recommendations"),
        BotCommand("trend", "Trend analysis"),
        BotCommand("report_day", "Daily summary report"),
        BotCommand("report_week", "Weekly summary report"),
        BotCommand("report_month", "Monthly summary report"),
    ])
    asyncio.create_task(_alert_loop(app))

def main():
    """Start the bot"""
    print("Starting bot...", flush=True)
    # Python 3.10+: main thread has no event loop by default; PTB's run_polling needs one
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(_post_init)
            .build()
        )
        print("Application created.", flush=True)

        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("readings", readings_command))
        application.add_handler(CommandHandler("diagnose", diagnose_command))
        application.add_handler(CommandHandler("trend", trend_command))
        application.add_handler(CommandHandler("report_day", report_day_command))
        application.add_handler(CommandHandler("report_week", report_week_command))
        application.add_handler(CommandHandler("report_month", report_month_command))
        application.add_handler(CallbackQueryHandler(button_callback))

        print("Bot is starting... (Press Ctrl+C to stop)", flush=True)
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()