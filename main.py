import os
import requests

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


async def btc(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"

    data = requests.get(url).json()

    price = data["bitcoin"]["usd"]

    await update.message.reply_text(
        f"📈 BTC\n\n{price:,.0f} USD"
    )


app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("btc", btc))

app.run_polling()
