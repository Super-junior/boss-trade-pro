import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🚀 BOSS TRADE MASTER BOT

📈 /btc - Bitcoin
🥇 /gold - Gold
💵 /dxy - DXY
🏦 /us10y - US10Y
🏛️ /etf - Bitcoin ETF
🧠 /boss - BOSS Score
"""
    await update.message.reply_text(text)

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.run_polling()
