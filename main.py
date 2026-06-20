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
# LIVE PRICE STORAGE  {chat_id: message_id}
# ─────────────────────────────────────────────
_live_messages: dict[int, int] = {}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_klines(symbol: str, interval: str = "1d", limit: int = 60) -> list[float]:
    """Trả về danh sách giá đóng cửa (close price) từ Binance klines."""
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval={interval}&limit={limit}"
    )
    data = requests.get(url, timeout=10).json()
    return [float(k[4]) for k in data]  # index 4 = close price


def calc_ema(prices: list[float], period: int) -> float:
    """Tính EMA của danh sách giá, trả về giá trị EMA cuối."""
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_rsi(prices: list[float], period: int = 14) -> float:
    """Tính RSI(period) từ danh sách giá đóng cửa."""
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_macd(prices: list[float]) -> tuple[float, float, float]:
    """Trả về (macd, signal, histogram) dùng EMA12, EMA26, Signal EMA9."""
    ema12  = calc_ema(prices[-26:], 12)
    ema26  = calc_ema(prices[-26:], 26)
    macd   = ema12 - ema26
    # Signal = EMA9 của MACD — xấp xỉ bằng cách tính trên 9 nến cuối
    macd_series = [
        calc_ema(prices[-(26 + 9 - i):-(i) if i else None], 12) -
        calc_ema(prices[-(26 + 9 - i):-(i) if i else None], 26)
        for i in range(8, -1, -1)
    ]
    signal    = calc_ema(macd_series, 9)
    histogram = macd - signal
    return round(macd, 2), round(signal, 2), round(histogram, 2)


def get_funding_rate() -> dict:
    """Lấy Funding Rate BTCUSDT từ Binance Futures (fapi)."""
    url = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
    d   = requests.get(url, timeout=10).json()
    from datetime import datetime, timezone
    next_ts = int(d["nextFundingTime"]) / 1000
    next_dt = datetime.fromtimestamp(next_ts, tz=timezone.utc)
    now_dt  = datetime.now(timezone.utc)
    countdown = next_dt - now_dt
    hours, rem  = divmod(int(countdown.total_seconds()), 3600)
    minutes     = rem // 60
    return {
        "mark_price":    float(d["markPrice"]),
        "index_price":   float(d["indexPrice"]),
        "funding_rate":  float(d["lastFundingRate"]) * 100,   # chuyển sang %
        "countdown":     f"{hours:02d}:{minutes:02d}",
    }


def get_order_book_ratio(limit: int = 20) -> dict:
    """Lấy order book BTCUSDT và tính tỷ lệ bid/ask theo khối lượng USDT."""
    url  = f"https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit={limit}"
    data = requests.get(url, timeout=10).json()
    bid_vol = sum(float(p) * float(q) for p, q in data["bids"])
    ask_vol = sum(float(p) * float(q) for p, q in data["asks"])
    total   = bid_vol + ask_vol
    bid_pct = bid_vol / total * 100
    ask_pct = ask_vol / total * 100
    return {
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "bid_pct": bid_pct,
        "ask_pct": ask_pct,
    }


def get_fear_greed() -> dict:
    """Lấy Crypto Fear & Greed Index từ alternative.me (miễn phí, không cần key)."""
    url = "https://api.alternative.me/fng/?limit=1"
    r = requests.get(url, timeout=10).json()
    d = r["data"][0]
    return {
        "value": int(d["value"]),
        "label": d["value_classification"],
    }


def get_binance(symbol: str) -> dict:
    """
    Gọi Binance /api/v3/ticker/24hr
    Trả về dict chuẩn hoá với các key:
      lastPrice, priceChangePercent, quoteVolume, highPrice, lowPrice
    """
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
    fng_d = get_fear_greed()

    btc_chg   = float(btc_d["priceChangePercent"])
    btc_price = float(btc_d["lastPrice"])
    etf_chg   = etf_d["change_pct"]
    dxy_chg   = dxy_d["change_pct"]
    t10_val   = t10_d["price"]
    t10_chg   = t10_d["change_pct"]
    gld_chg   = gld_d["change_pct"]
    fng_val   = fng_d["value"]
    fng_lbl   = fng_d["label"]

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
    # Fear & Greed: tham lam → tích cực, sợ hãi → tiêu cực
    if fng_val >= 75:
        score += 1.0
    elif fng_val >= 55:
        score += 0.5
    elif fng_val >= 45:
        score += 0.0
    elif fng_val >= 25:
        score -= 0.5
    else:
        score -= 1.0
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
        f"🥇 Gold 1d:   `{gld_chg:>+.2f}%`\n"
        f"😱 F&G Index: `{fng_val}/100`  ({fng_lbl})\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ SCORE:  `{score:>+.1f} / 10`\n"
        f"{emoji} [{bar}]\n"
        f"🏷️ Tín hiệu: *{label}*"
    )


# ─────────────────────────────────────────────
# BACKGROUND JOB — kiểm tra alert mỗi 60 giây
# ─────────────────────────────────────────────

# Cache funding rate — chỉ gọi lại mỗi 5 phút vì chỉ đổi 8h/lần
_funding_cache: dict = {}
_funding_cache_ts: float = 0.0


def _get_funding_cached() -> dict:
    import time
    global _funding_cache, _funding_cache_ts
    if time.time() - _funding_cache_ts > 300:
        _funding_cache    = get_funding_rate()
        _funding_cache_ts = time.time()
    return _funding_cache


def _build_live_text(spot: dict, dom: dict, fund: dict) -> str:
    """Tạo nội dung live dashboard từ spot ticker + order book + funding."""
    from datetime import datetime, timezone
    price      = float(spot["lastPrice"])
    change_pct = float(spot["priceChangePercent"])
    change_usd = float(spot["priceChange"])
    high       = float(spot["highPrice"])
    low        = float(spot["lowPrice"])
    bid        = float(spot["bidPrice"])
    ask        = float(spot["askPrice"])
    volume     = float(spot["quoteVolume"])
    trades     = int(spot["count"])
    now        = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    arr  = "▲" if change_pct >= 0 else "▼"
    sign = "🟢" if change_pct >= 0 else "🔴"
    spread = ask - bid

    # DOM bar
    bid_pct  = dom["bid_pct"]
    ask_pct  = dom["ask_pct"]
    bid_vol  = dom["bid_vol"]
    ask_vol  = dom["ask_vol"]
    bid_bars = round(bid_pct / 10)
    dom_bar  = "🟩" * bid_bars + "🟥" * (10 - bid_bars)

    # Funding
    frate     = fund["funding_rate"]
    countdown = fund["countdown"]
    f_sign    = "+" if frate >= 0 else ""
    f_emoji   = "🟢" if frate > 0 else ("🔴" if frate < 0 else "⚪")

    return (
        f"⚡ *BTC/USDT — Bảng Giá Thời Gian Thực*\n\n"
        f"{sign} *`{fmt_price(price)}`* USDT  {arr}\n"
        f"📊 24h: `{change_pct:>+.2f}%`  (`{change_usd:>+,.2f}` USDT)\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔼 Cao 24h:  `{fmt_price(high)}` USDT\n"
        f"🔽 Thấp 24h: `{fmt_price(low)}` USDT\n"
        f"🟩 Bid:      `{fmt_price(bid)}` USDT\n"
        f"🟥 Ask:      `{fmt_price(ask)}` USDT\n"
        f"↔️ Spread:   `{fmt_price(spread)}` USDT\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📖 *Order Book (Top 20)*\n"
        f"{dom_bar}\n"
        f"🟩 Mua `{bid_pct:.1f}%` ({bid_vol/1_000_000:.1f}M)  "
        f"🟥 Bán `{ask_pct:.1f}%` ({ask_vol/1_000_000:.1f}M)\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💸 *Futures Funding Rate*\n"
        f"{f_emoji} `{f_sign}{frate:.4f}%` / 8h  ⏳ còn `{countdown}`\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📦 KL 24h: `{volume/1_000_000_000:.2f}` tỷ USDT\n"
        f"🔢 Lệnh:   `{trades:,}`\n\n"
        f"🕐 `{now}`  _/stoplive để dừng_"
    )


async def _update_live_price(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job — cập nhật live dashboard mỗi 5 giây."""
    chat_id    = context.job.chat_id
    message_id = _live_messages.get(chat_id)
    if message_id is None:
        return
    try:
        spot = get_binance("BTCUSDT")
        dom  = get_order_book_ratio(limit=20)
        fund = _get_funding_cached()
        text = _build_live_text(spot, dom, fund)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="Markdown",
        )
    except Exception:
        pass


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
        "🧠 /boss         — BOSS Score tổng hợp\n"
        "😱 /fear         — Fear & Greed Index\n"
        "📊 /signal       — Tín hiệu kỹ thuật (RSI, MA, MACD)\n"
        "⚡ /live         — Theo dõi giá BTC thời gian thực\n"
        "🛑 /stoplive     — Dừng theo dõi live\n"
        "💸 /funding      — Funding Rate BTC Futures\n"
        "📖 /dom          — Tỷ lệ mua/bán Order Book\n"
        "🚀 /topgain      — Top 5 coin tăng mạnh nhất 24h\n"
        "💀 /toploss      — Top 5 coin giảm mạnh nhất 24h\n"
        "🌍 /market       — Tổng quan thị trường crypto\n\n"
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
    data = get_binance("BTCUSDT")

    price      = float(data["lastPrice"])
    change_pct = float(data["priceChangePercent"])
    change_usd = float(data["priceChange"])
    open_price = float(data["openPrice"])
    high       = float(data["highPrice"])
    low        = float(data["lowPrice"])
    vwap       = float(data["weightedAvgPrice"])
    volume     = float(data["quoteVolume"])
    bid        = float(data["bidPrice"])
    ask        = float(data["askPrice"])
    trades     = int(data["count"])

    spread     = ask - bid

    text = (
        f"📈 *BTC/USDT — Binance*\n\n"
        f"💰 Giá hiện tại: `{fmt_price(price)}` USDT\n"
        f"📊 Thay đổi 24h: `{change_pct:>+.2f}%`  (`{change_usd:>+,.2f}` USDT) {arrow(change_pct)}\n\n"
        f"🔓 Mở cửa:     `{fmt_price(open_price)}` USDT\n"
        f"🔼 Cao nhất:   `{fmt_price(high)}` USDT\n"
        f"🔽 Thấp nhất:  `{fmt_price(low)}` USDT\n"
        f"📐 VWAP:       `{fmt_price(vwap)}` USDT\n\n"
        f"🟩 Bid tốt nhất: `{fmt_price(bid)}` USDT\n"
        f"🟥 Ask tốt nhất: `{fmt_price(ask)}` USDT\n"
        f"↔️ Spread:       `{fmt_price(spread)}` USDT\n\n"
        f"📦 KL 24h:    `{volume / 1_000_000_000:.2f}` tỷ USDT\n"
        f"🔢 Số lệnh:   `{trades:,}` lệnh"
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


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    job_name = f"live_price_{chat_id}"

    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    spot = get_binance("BTCUSDT")
    dom  = get_order_book_ratio(limit=20)
    fund = _get_funding_cached()
    text = _build_live_text(spot, dom, fund)
    sent = await update.message.reply_text(text, parse_mode="Markdown")

    _live_messages[chat_id] = sent.message_id

    context.job_queue.run_repeating(
        _update_live_price,
        interval=5,
        first=5,
        chat_id=chat_id,
        name=job_name,
    )


async def stoplive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    job_name = f"live_price_{chat_id}"

    jobs = context.job_queue.get_jobs_by_name(job_name)
    if jobs:
        for job in jobs:
            job.schedule_removal()
        _live_messages.pop(chat_id, None)
        await update.message.reply_text("✅ Đã dừng theo dõi giá BTC thời gian thực.")
    else:
        await update.message.reply_text("📭 Không có luồng live nào đang chạy.")


async def funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = get_funding_rate()
    rate      = d["funding_rate"]
    mark      = d["mark_price"]
    index     = d["index_price"]
    countdown = d["countdown"]

    if rate > 0.01:
        rate_emoji = "🟢"
        rate_note  = "Long đang trả cho Short — thị trường thiên long"
    elif rate > 0:
        rate_emoji = "🟡"
        rate_note  = "Funding dương nhẹ — cân bằng, thiên long"
    elif rate == 0:
        rate_emoji = "⚪"
        rate_note  = "Funding bằng 0 — thị trường trung lập"
    elif rate > -0.01:
        rate_emoji = "🟠"
        rate_note  = "Funding âm nhẹ — thiên short"
    else:
        rate_emoji = "🔴"
        rate_note  = "Short đang trả cho Long — thị trường thiên short mạnh"

    premium = mark - index
    text = (
        f"💸 *BTC Futures — Funding Rate*\n\n"
        f"{rate_emoji} Funding Rate: `{rate:>+.4f}%`  (mỗi 8 giờ)\n"
        f"💡 {rate_note}\n\n"
        f"📍 Mark Price:   `{fmt_price(mark)}` USDT\n"
        f"📍 Index Price:  `{fmt_price(index)}` USDT\n"
        f"↔️ Premium:      `{premium:>+.2f}` USDT\n\n"
        f"⏳ Funding tiếp theo: `{countdown}` nữa"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def dom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d       = get_order_book_ratio(limit=20)
    bid_pct = d["bid_pct"]
    ask_pct = d["ask_pct"]
    bid_vol = d["bid_vol"]
    ask_vol = d["ask_vol"]

    bid_bars = round(bid_pct / 10)
    ask_bars = 10 - bid_bars
    bar = "🟩" * bid_bars + "🟥" * ask_bars

    if bid_pct >= 60:
        verdict = "🟢 Áp lực MUA mạnh"
    elif bid_pct >= 52:
        verdict = "🟡 Thiên MUA nhẹ"
    elif bid_pct >= 48:
        verdict = "⚪ Cân bằng"
    elif bid_pct >= 40:
        verdict = "🟠 Thiên BÁN nhẹ"
    else:
        verdict = "🔴 Áp lực BÁN mạnh"

    text = (
        f"📖 *BTC Order Book — Top 20*\n\n"
        f"{bar}\n\n"
        f"🟩 Mua (Bid): `{bid_pct:.1f}%`  —  `{bid_vol/1_000_000:.2f}M` USDT\n"
        f"🟥 Bán (Ask): `{ask_pct:.1f}%`  —  `{ask_vol/1_000_000:.2f}M` USDT\n\n"
        f"⚡ {verdict}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


def get_top_movers(top_n: int = 5) -> tuple[list[dict], list[dict]]:
    """Lấy toàn bộ USDT ticker từ Binance, trả về top tăng và top giảm."""
    url  = "https://api.binance.com/api/v3/ticker/24hr"
    data = requests.get(url, timeout=15).json()
    usdt = [
        t for t in data
        if t["symbol"].endswith("USDT")
        and not t["symbol"].endswith("DOWNUSDT")
        and not t["symbol"].endswith("UPUSDT")
        and float(t["quoteVolume"]) > 1_000_000
    ]
    usdt.sort(key=lambda t: float(t["priceChangePercent"]), reverse=True)
    return usdt[:top_n], usdt[-top_n:][::-1]


async def topgain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gainers, _ = get_top_movers(5)
    lines = ["🚀 *Top 5 Coin Tăng Mạnh Nhất 24h (Binance)*\n"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, t in enumerate(gainers):
        symbol  = t["symbol"].replace("USDT", "/USDT")
        chg     = float(t["priceChangePercent"])
        price   = float(t["lastPrice"])
        vol     = float(t["quoteVolume"]) / 1_000_000
        lines.append(
            f"{medals[i]} *{symbol}*\n"
            f"   💰 `{fmt_price(price)}` USDT  📊 `+{chg:.2f}%`\n"
            f"   📦 KL: `{vol:.1f}M` USDT"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def toploss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, losers = get_top_movers(5)
    lines = ["💀 *Top 5 Coin Giảm Mạnh Nhất 24h (Binance)*\n"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, t in enumerate(losers):
        symbol  = t["symbol"].replace("USDT", "/USDT")
        chg     = float(t["priceChangePercent"])
        price   = float(t["lastPrice"])
        vol     = float(t["quoteVolume"]) / 1_000_000
        lines.append(
            f"{medals[i]} *{symbol}*\n"
            f"   💰 `{fmt_price(price)}` USDT  📊 `{chg:.2f}%`\n"
            f"   📦 KL: `{vol:.1f}M` USDT"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def get_market_overview() -> dict:
    """Lấy tổng quan thị trường: BTC dominance + total mcap từ CoinGecko, gainers/losers từ Binance."""
    cg = requests.get(
        "https://api.coingecko.com/api/v3/global",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    ).json()["data"]

    total_mcap   = cg["total_market_cap"]["usd"]
    total_vol    = cg["total_volume"]["usd"]
    btc_dom      = cg["market_cap_percentage"]["btc"]
    eth_dom      = cg["market_cap_percentage"]["eth"]
    mcap_chg_24h = cg["market_cap_change_percentage_24h_usd"]

    tickers = requests.get(
        "https://api.binance.com/api/v3/ticker/24hr",
        timeout=15,
    ).json()
    usdt = [
        t for t in tickers
        if t["symbol"].endswith("USDT")
        and not t["symbol"].endswith(("DOWNUSDT", "UPUSDT"))
        and float(t["quoteVolume"]) > 500_000
    ]
    gainers = sum(1 for t in usdt if float(t["priceChangePercent"]) > 0)
    losers  = sum(1 for t in usdt if float(t["priceChangePercent"]) < 0)
    total   = len(usdt)

    return {
        "total_mcap":   total_mcap,
        "total_vol":    total_vol,
        "btc_dom":      btc_dom,
        "eth_dom":      eth_dom,
        "mcap_chg_24h": mcap_chg_24h,
        "gainers":      gainers,
        "losers":       losers,
        "total":        total,
    }


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang tổng hợp dữ liệu thị trường…")
    d = get_market_overview()

    mcap_t  = d["total_mcap"] / 1e12
    vol_b   = d["total_vol"]  / 1e9
    chg     = d["mcap_chg_24h"]
    chg_arr = "▲" if chg >= 0 else "▼"
    chg_ico = "🟢" if chg >= 0 else "🔴"

    gainer_bar = round(d["gainers"] / d["total"] * 10)
    loser_bar  = 10 - gainer_bar
    bar        = "🟢" * gainer_bar + "🔴" * loser_bar

    if d["gainers"] > d["losers"] * 1.5:
        mood = "🟢 Thị trường XANH — tâm lý lạc quan"
    elif d["losers"] > d["gainers"] * 1.5:
        mood = "🔴 Thị trường ĐỎ — tâm lý bi quan"
    else:
        mood = "🟡 Thị trường TRUNG LẬP — cân bằng"

    text = (
        f"🌍 *Tổng Quan Thị Trường Crypto*\n\n"
        f"💹 Tổng vốn hóa: `${mcap_t:.2f}T`\n"
        f"{chg_ico} Thay đổi 24h: `{chg:>+.2f}%` {chg_arr}\n"
        f"📦 Volume 24h:  `${vol_b:.1f}B`\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"₿  BTC Dominance: `{d['btc_dom']:.1f}%`\n"
        f"Ξ  ETH Dominance: `{d['eth_dom']:.1f}%`\n"
        f"🔵 Alt Dominance: `{100 - d['btc_dom'] - d['eth_dom']:.1f}%`\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 Binance USDT — {d['total']} cặp\n"
        f"{bar}\n"
        f"🟢 Tăng: `{d['gainers']}`  🔴 Giảm: `{d['losers']}`\n\n"
        f"⚡ {mood}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang tính chỉ báo kỹ thuật…")

    closes = get_klines("BTCUSDT", interval="1d", limit=60)
    price  = closes[-1]

    rsi  = calc_rsi(closes)
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    macd, sig_line, hist = calc_macd(closes)

    bulls = 0
    bears = 0

    if rsi < 30:
        rsi_label = "🟢 Quá bán — BUY"
        bulls += 1
    elif rsi > 70:
        rsi_label = "🔴 Quá mua — SELL"
        bears += 1
    elif rsi >= 50:
        rsi_label = "🟡 Trung lập nghiêng tăng"
        bulls += 1
    else:
        rsi_label = "🟠 Trung lập nghiêng giảm"
        bears += 1

    if price > sma20:
        sma20_label = "🟢 Giá trên SMA20 — tăng"
        bulls += 1
    else:
        sma20_label = "🔴 Giá dưới SMA20 — giảm"
        bears += 1

    if price > sma50:
        sma50_label = "🟢 Giá trên SMA50 — xu hướng tăng"
        bulls += 1
    else:
        sma50_label = "🔴 Giá dưới SMA50 — xu hướng giảm"
        bears += 1

    if sma20 > sma50:
        cross_label = "🟢 Golden Cross (SMA20 > SMA50)"
        bulls += 1
    else:
        cross_label = "🔴 Death Cross (SMA20 < SMA50)"
        bears += 1

    if macd > sig_line:
        macd_label = "🟢 MACD trên Signal — momentum tăng"
        bulls += 1
    else:
        macd_label = "🔴 MACD dưới Signal — momentum giảm"
        bears += 1

    total = bulls + bears
    if bulls >= 4:
        verdict = "🟢 *TỔNG HỢP: TĂNG (BULLISH)*"
    elif bears >= 4:
        verdict = "🔴 *TỔNG HỢP: GIẢM (BEARISH)*"
    else:
        verdict = "🟡 *TỔNG HỢP: TRUNG LẬP (NEUTRAL)*"

    text = (
        f"📊 *BTC — Tín Hiệu Kỹ Thuật (1D)*\n\n"
        f"💰 Giá: `{fmt_price(price)}` USDT\n\n"
        f"*RSI(14):* `{rsi}`\n"
        f"  {rsi_label}\n\n"
        f"*SMA20:* `{fmt_price(sma20)}`\n"
        f"  {sma20_label}\n\n"
        f"*SMA50:* `{fmt_price(sma50)}`\n"
        f"  {sma50_label}\n\n"
        f"*MA Cross:*\n"
        f"  {cross_label}\n\n"
        f"*MACD:* `{macd:+.0f}`  Signal: `{sig_line:+.0f}`  Hist: `{hist:+.0f}`\n"
        f"  {macd_label}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🐂 Tăng: `{bulls}/{total}`  🐻 Giảm: `{bears}/{total}`\n"
        f"{verdict}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def fear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data  = get_fear_greed()
    value = data["value"]
    label = data["label"]

    if value >= 75:
        emoji = "🟢"
        vi_label = "THAM LAM CỰC ĐỘ"
        note = "⚠️ Thị trường quá hưng phấn — cẩn thận bán đỉnh"
    elif value >= 55:
        emoji = "🟡"
        vi_label = "THAM LAM"
        note = "📈 Tâm lý tích cực — theo dõi đà tăng"
    elif value >= 45:
        emoji = "🟠"
        vi_label = "TRUNG LẬP"
        note = "➡️ Thị trường cân bằng — chờ tín hiệu rõ"
    elif value >= 25:
        emoji = "🔴"
        vi_label = "SỢ HÃI"
        note = "💡 Cơ hội mua dần — thị trường đang bi quan"
    else:
        emoji = "🔴"
        vi_label = "SỢ HÃI CỰC ĐỘ"
        note = "🚨 Hoảng loạn cực độ — thường là đáy tốt để tích lũy"

    bar_pos = int(value / 10)
    bar     = "█" * bar_pos + "░" * (10 - bar_pos)

    text = (
        f"😱 *Fear & Greed Index — Crypto*\n\n"
        f"{emoji} Chỉ số: `{value} / 100`\n"
        f"🏷️ Trạng thái: *{vi_label}*\n"
        f"📊 [{bar}]\n\n"
        f"💡 {note}\n\n"
        f"_Nguồn: alternative.me_"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ──────────────────────────────────────────────
# SCHEDULE COMMANDS
# ──────────────────────────────────────────────

async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = f"boss_schedule_{chat_id}"

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

    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

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
    app.add_handler(CommandHandler("fear",     fear))
    app.add_handler(CommandHandler("signal",   signal))
    app.add_handler(CommandHandler("live",     live))
    app.add_handler(CommandHandler("stoplive", stoplive))
    app.add_handler(CommandHandler("funding",  funding))
    app.add_handler(CommandHandler("dom",      dom))
    app.add_handler(CommandHandler("topgain",  topgain))
    app.add_handler(CommandHandler("toploss",  toploss))
    app.add_handler(CommandHandler("market",   market))
    app.add_handler(CommandHandler("alert",    alert))
    app.add_handler(CommandHandler("alerts",   alerts))
    app.add_handler(CommandHandler("delalert", delalert))
    app.add_handler(CommandHandler("schedule", schedule))

    app.job_queue.run_repeating(_check_alerts, interval=60, first=10)

    print("✅ BOSS TRADE BOT đang chạy…")
    app.run_polling()


if __name__ == "__main__":
    main()
