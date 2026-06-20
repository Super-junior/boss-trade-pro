import os
import requests

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv(“BOT_TOKEN”)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
text = “””
🚀 BOSS TRADE MASTER BOT

📈 /btc - Bitcoin
🏛️ /etf - Bitcoin ETF
💵 /dxy - DXY
🏦 /us10y - US10Y
🥇 /gold - Gold
🧠 /boss - BOSS Score
“””
await update.message.reply_text(text)

async def btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
url = “https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT”
