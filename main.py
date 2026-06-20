"""
BOSS TRADE MASTER BOT
─────────────────────
Lệnh:
  /start   — Menu chính
  /btc     — Giá Bitcoin (Binance)
  /etf     — Bitcoin ETF IBIT (BlackRock)
  /dxy     — DXY Dollar Index
  /us10y   — US 10‑Year Treasury Yield
  /gold    — Vàng XAU/USD
  /boss    — BOSS Score tổng hợp macro
  /alert <giá>      — Đặt cảnh báo giá BTC (VD: /alert 70000)
  /alerts           — Xem danh sách alert đang chạy
  /delalert <id>    — Xóa alert theo ID  (VD: /delalert 3)
  /delalert all     — Xóa tất cả alerts
  /schedule <HH:MM> — Đặt lịch tự động gửi BOSS Score (VD: /schedule 08:00)
  /schedule off     — Tắt lịch tự động
  /schedule         — Xem lịch hiện tại

Cài đặt:
  pip install python-telegram-bot requests

Chạy:
  BOT_TOKEN=<token> python boss_trade_bot.py
"""

import os
import logging
import requests
from datetime import time as dt_time
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ─────────────────────────────────────────────
# ALERT STORAGE  {chat_id: [{id, target, direction}]}
# ─────────────────────────────────────────────
_alert_counter: dict[int, int] = defaultdict(int)
_alerts: dict[int, list[dict]] = defaultdict(list)

# ─────────────────────────────────────────────
# SCHEDULE STORAGE  {chat_id: "HH:MM"}
# ─────────────────────────────────────────────
_schedules: dict[int, str] = {}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_binance(symbol: str) -> dict:
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
    return requests.get(url, timeout=10).json()


def get_yahoo(ticker: str) -> dict:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        "?interval=1d&range=2d"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=10).json()
    meta = r["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev  = meta["chartPreviousClose"]
    return {
        "price": price,
        "prev": prev,
        "change_pct": (price - prev) / prev * 100,
    }


def arrow(val: float) -> str:
    return "🟢 ▲" if val >= 0 else "🔴 ▼"


def fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def build_boss_text() -> str:
    """Fetch all data và trả về chuỗi BOSS Score. Dùng chung cho /boss và schedule."""
    btc_d = get_binance("BTCUSDT")
    etf_d = get_yahoo("IBIT")
    dxy_d = get_yahoo("DX-Y.NYB")
    t10_d = get_yahoo("%5ETNX")
    gld_d = get_yahoo("GC%3DF")

    btc_chg   = float(btc_d["priceChangePercent"])
    btc_price = float(btc_d["lastPrice"])
    etf_chg   = etf_d["change_pct"]
    dxy_chg   = dxy_d["change_pct"]
    t10_val   = t10_d["price"]
    t10_chg   = t10_d["change_pct"]
    gld_chg   = gld_d["change_pct"]

    score = 0.0
    score += max(-3.0, min(3.0, btc_chg))
    score += 1.0 if etf_chg > 0 else -1.0
    score -= max(-1.0, min(1.0, dxy_chg))
    if t10_val > 4.0:
        score -= min(2.0, (t10_val - 4.0) * 0.5)
    if gld_chg > 0.3:
        score += 1.0
    elif gld_chg > 0:
        score += 0.5
    elif gld_chg < -0.3:
        score -= 1.0
    else:
        score -= 0.5
    score = round(score, 1)

    if score >= 4:
        label, emoji = "STRONG BUY 🚀", "🟢"
    elif score >= 2:
        label, emoji = "BUY 📈", "🟢"
    elif score >= 0:
        label, emoji = "NEUTRAL ➡️", "🟡"
    elif score >= -2:
        label, emoji = "CAUTION ⚠️", "🟠"
    else:
        label, emoji = "SELL / AVOID 🚨", "🔴"

    bar_pos = int(min(10, max(0, (score + 10) / 20 * 10)))
    bar     = "█" * bar_pos + "░" * (10 - bar_pos)

    return (
        f"🧠 *BOSS SCORE — Tổng Hợp Macro*\n\n"
        f"📈 BTC 24h:   `{btc_chg:>+.2f}%`  ({fmt_price(btc_price)} USDT)\n"
        f"🏛️ ETF 1d:    `{etf_chg:>+.2f}%`\n"
        f"💵 DXY 1d:    `{dxy_chg:>+.2f}%`\n"
        f"🏦 US10Y:     `{t10_val:.3f}%`  (`{t10_chg:>+.2f}%`)\n"
        f"🥇 Gold 1d:   `{gld_chg:>+.2f}%`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ SCORE:  `{score:>+.1f} / 10`\n"
        f"{emoji} [{bar}]\n"
        f"🏷️ Tín hiệu: *{label}*"
    )


# ─────────────────────────────────────────────
# BACKGROUND JOB — kiểm tra alert mỗi 60 giây
# ─────────────────────────────────────────────

async def _check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _alerts:
        return
    try:
        data  = get_binance("BTCUSDT")
        price = float(data["lastPrice"])
    except Exception:
        return

    for chat_id, alerts in list(_alerts.items()):
        triggered = []
        remaining = []
        for a in alerts:
            hit = (
                (a["direction"] == "above" and price >= a["target"])
                or
                (a["direction"] == "below" and price <= a["target"])
            )
            if hit:
                triggered.append(a)
            else:
                remaining.append(a)

        _alerts[chat_id] = remaining

        for a in triggered:
            direction_txt = "vượt lên trên" if a["direction"] == "above" else "giảm xuống dưới"
            msg = (
                f"🔔 *ALERT #{a['id']} kích hoạt!*\n\n"
                f"BTC đã {direction_txt} ngưỡng `{fmt_price(a['target'])}` USDT\n"
                f"💰 Giá hiện tại: `{fmt_price(price)}` USDT"
            )
            await context.bot.send_message(
                chat_id=chat_id, text=msg, parse_mode="Markdown"
            )


# ─────────────────────────────────────────────
# BACKGROUND JOB — gửi BOSS Score theo lịch
# ─────────────────────────────────────────────

async def _send_scheduled_boss(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    try:
        text = build_boss_text()
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Không lấy được dữ liệu: `{e}`",
            parse_mode="Markdown",
        )
        return
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🚀 *BOSS TRADE MASTER BOT*\n\n"
        "📈 /btc          — Bitcoin (Binance)\n"
        "🏛️ /etf          — Bitcoin ETF (IBIT)\n"
        "💵 /dxy          — DXY Dollar Index\n"
        "🏦 /us10y        — US 10‑Year Yield\n"
        "🥇 /gold         — Vàng (XAU/USD)\n"
        "🧠 /boss         — BOSS Score tổng hợp\n\n"
        "🔔 *Alert BTC:*\n"
        "/alert `<giá>`     — Đặt cảnh báo giá\n"
        "/alerts            — Xem danh sách\n"
        "/delalert `<id>`   — Xóa theo ID\n"
        "/delalert `all`    — Xóa tất cả\n\n"
        "⏰ *Lịch tự động:*\n"
        "/schedule `<HH:MM>` — Đặt giờ gửi BOSS Score\n"
        "/schedule `off`     — Tắt lịch\n"
        "/schedule           — Xem lịch hiện tại"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data   = get_binance("BTCUSDT")
    price  = float(data["lastPrice"])
    change = float(data["priceChangePercent"])
    volume = float(data["quoteVolume"]) / 1_000_000_000
    high   = float(data["highPrice"])
    low    = float(data["lowPrice"])
    text = (
        f"📈 *BTC/USDT — Binance*\n\n"
        f"💰 Giá:       `{fmt_price(price)}` USDT\n"
        f"📊 24h:       `{change:>+.2f}%` {arrow(change)}\n"
        f"🔼 Cao nhất:  `{fmt_price(high)}` USDT\n"
        f"🔽 Thấp nhất: `{fmt_price(low)}` USDT\n"
        f"📦 KL 24h:    `{volume:.2f}` tỷ USDT"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def etf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data   = get_yahoo("IBIT")
    price  = data["price"]
    change = data["change_pct"]
    text = (
        f"🏛️ *Bitcoin ETF — IBIT (BlackRock)*\n\n"
        f"💰 Giá:  `{price:.2f}` USD\n"
        f"📊 1d:   `{change:>+.2f}%` {arrow(change)}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def dxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data      = get_yahoo("DX-Y.NYB")
    price     = data["price"]
    change    = data["change_pct"]
    sentiment = (
        "⚠️ Đô mạnh — áp lực lên BTC/vàng"
        if change >= 0
        else "✅ Đô yếu — hỗ trợ BTC/vàng"
    )
    text = (
        f"💵 *DXY — US Dollar Index*\n\n"
        f"📍 Giá:     `{price:.3f}`\n"
        f"📊 1d:      `{change:>+.2f}%` {arrow(change)}\n"
        f"💡 {sentiment}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def us10y(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data      = get_yahoo("%5ETNX")
    price     = data["price"]
    change    = data["change_pct"]
    sentiment = (
        "⚠️ Lợi suất cao — áp lực lên BTC"
        if price > 4.5
        else "✅ Lợi suất ổn định"
    )
    text = (
        f"🏦 *US 10‑Year Treasury Yield*\n\n"
        f"📍 Yield:  `{price:.3f}%`\n"
        f"📊 1d:     `{change:>+.2f}%` {arrow(change)}\n"
        f"💡 {sentiment}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data   = get_yahoo("GC%3DF")
    price  = data["price"]
    change = data["change_pct"]
    text = (
        f"🥇 *GOLD — XAU/USD (Futures)*\n\n"
        f"💰 Giá:  `{fmt_price(price)}` USD/oz\n"
        f"📊 1d:   `{change:>+.2f}%` {arrow(change)}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def boss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang tổng hợp dữ liệu…")
    text = build_boss_text()
    await update.message.reply_text(text, parse_mode="Markdown")


# ──────────────────────────────────────────────
# SCHEDULE COMMANDS
# ──────────────────────────────────────────────

async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = f"boss_schedule_{chat_id}"

    # /schedule (không có arg) — xem lịch hiện tại
    if not context.args:
        current = _schedules.get(chat_id)
        if current:
            await update.message.reply_text(
                f"⏰ Lịch hiện tại: *{current}* (UTC)\n"
                f"Tắt lịch: `/schedule off`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "📭 Chưa đặt lịch.\n"
                "Đặt lịch: `/schedule 08:00`",
                parse_mode="Markdown",
            )
        return

    arg = context.args[0].lower()

    # /schedule off — tắt lịch
    if arg == "off":
        jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in jobs:
            job.schedule_removal()
        if chat_id in _schedules:
            del _schedules[chat_id]
            await update.message.reply_text("✅ Đã tắt lịch tự động.")
        else:
            await update.message.reply_text("📭 Chưa có lịch nào để tắt.")
        return

    # /schedule HH:MM — đặt lịch mới
    try:
        parts = arg.split(":")
        if len(parts) != 2:
            raise ValueError
        hh, mm = int(parts[0]), int(parts[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Định dạng không hợp lệ.\nDùng: `/schedule HH:MM`  VD: `/schedule 08:00`",
            parse_mode="Markdown",
        )
        return

    # Xóa job cũ nếu có
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    # Đặt job mới chạy mỗi ngày theo giờ UTC
    run_time = dt_time(hour=hh, minute=mm)
    context.job_queue.run_daily(
        _send_scheduled_boss,
        time=run_time,
        chat_id=chat_id,
        name=job_name,
    )
    _schedules[chat_id] = f"{hh:02d}:{mm:02d}"

    await update.message.reply_text(
        f"✅ *Đã đặt lịch!*\n\n"
        f"⏰ Bot sẽ gửi BOSS Score lúc *{hh:02d}:{mm:02d} UTC* mỗi ngày.\n\n"
        f"_Xem lịch: /schedule_\n"
        f"_Tắt lịch: /schedule off_",
        parse_mode="Markdown",
    )


# ──────────────────────────────────────────────
# ALERT COMMANDS
# ──────────────────────────────────────────────

async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "❌ Dùng: `/alert <giá>`\nVí dụ: `/alert 70000`",
            parse_mode="Markdown",
        )
        return

    try:
        target = float(context.args[0].replace(",", ""))
        if target <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Giá không hợp lệ. Nhập số dương, ví dụ: `/alert 70000`",
            parse_mode="Markdown",
        )
        return

    data  = get_binance("BTCUSDT")
    price = float(data["lastPrice"])

    if price == target:
        await update.message.reply_text(
            f"⚠️ BTC đang ở đúng `{fmt_price(target)}` USDT rồi!",
            parse_mode="Markdown",
        )
        return

    direction = "above" if target > price else "below"
    _alert_counter[chat_id] += 1
    alert_id = _alert_counter[chat_id]
    _alerts[chat_id].append({"id": alert_id, "target": target, "direction": direction})

    dir_txt = "vượt lên trên" if direction == "above" else "giảm xuống dưới"
    text = (
        f"🔔 *Alert #{alert_id} đã đặt!*\n\n"
        f"Sẽ thông báo khi BTC {dir_txt}\n"
        f"🎯 Ngưỡng: `{fmt_price(target)}` USDT\n"
        f"💰 Giá hiện tại: `{fmt_price(price)}` USDT\n\n"
        f"Xem danh sách: /alerts\n"
        f"Xóa alert: `/delalert {alert_id}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active  = _alerts.get(chat_id, [])

    if not active:
        await update.message.reply_text(
            "📭 Chưa có alert nào.\nĐặt alert: `/alert <giá>`",
            parse_mode="Markdown",
        )
        return

    lines = ["🔔 *Danh sách Alert BTC đang chạy:*\n"]
    for a in active:
        dir_txt = "↑ vượt lên" if a["direction"] == "above" else "↓ giảm xuống"
        lines.append(f"  `#{a['id']}` — {dir_txt} `{fmt_price(a['target'])}` USDT")
    lines.append("\n_Xóa: /delalert <id>  hoặc  /delalert all_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def delalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "❌ Dùng: `/delalert <id>` hoặc `/delalert all`",
            parse_mode="Markdown",
        )
        return

    arg = context.args[0].lower()

    if arg == "all":
        count = len(_alerts.get(chat_id, []))
        _alerts[chat_id] = []
        await update.message.reply_text(
            f"🗑️ Đã xóa {count} alert." if count else "📭 Không có alert nào để xóa."
        )
        return

    try:
        target_id = int(arg)
    except ValueError:
        await update.message.reply_text(
            "❌ ID không hợp lệ. Ví dụ: `/delalert 3`",
            parse_mode="Markdown",
        )
        return

    before = len(_alerts[chat_id])
    _alerts[chat_id] = [a for a in _alerts[chat_id] if a["id"] != target_id]
    after  = len(_alerts[chat_id])

    if before == after:
        await update.message.reply_text(
            f"❌ Không tìm thấy alert #{target_id}.\nXem danh sách: /alerts"
        )
    else:
        await update.message.reply_text(f"✅ Đã xóa alert #{target_id}.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError(
            "Thiếu BOT_TOKEN!\n"
            "Chạy: BOT_TOKEN=<token> python boss_trade_bot.py"
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("btc",      btc))
    app.add_handler(CommandHandler("etf",      etf))
    app.add_handler(CommandHandler("dxy",      dxy))
    app.add_handler(CommandHandler("us10y",    us10y))
    app.add_handler(CommandHandler("gold",     gold))
    app.add_handler(CommandHandler("boss",     boss))
    app.add_handler(CommandHandler("alert",    alert))
    app.add_handler(CommandHandler("alerts",   alerts))
    app.add_handler(CommandHandler("delalert", delalert))
    app.add_handler(CommandHandler("schedule", schedule))

    # Kiểm tra alerts mỗi 60 giây
    app.job_queue.run_repeating(_check_alerts, interval=60, first=10)

    print("✅ BOSS TRADE BOT đang chạy…")
    app.run_polling()


if __name__ == "__main__":
    main()
