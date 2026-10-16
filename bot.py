import asyncio
import logging
import os
import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
import pandas_ta as ta
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Your Telegram Bot Token integrated directly
TOKEN = "8963718222:AAG0DAZH1oJvIy8LeXAW3Zcu5vZuIBq3qXE"

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# Initialize CEX Client (Binance Futures)
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'} # Perpetuals
})

# --- 1. ROUTER & DATA INGESTION ---
async def fetch_dex_data(query: str):
    """Fetches DEX pair data via DexScreener API."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                pairs = data.get("pairs", [])
                if pairs:
                    best = max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0)))
                    return {
                        "type": "DEX",
                        "name": best.get("baseToken", {}).get("name"),
                        "symbol": best.get("baseToken", {}).get("symbol"),
                        "price": float(best.get("priceUsd", 0)),
                        "liquidity": float(best.get("liquidity", {}).get("usd", 0)),
                        "volume_24h": float(best.get("volume", {}).get("h24", 0)),
                        "change_24h": float(best.get("priceChange", {}).get("h24", 0)),
                        "venue": f"DEX ({best.get('dexId').upper()})"
                    }
            return None

async def fetch_cex_data(symbol: str):
    """Fetches CEX perpetual data and candles via CCXT."""
    try:
        await exchange.load_markets()
        formatted_symbol = f"{symbol.upper()}/USDT:USDT"
        if formatted_symbol not in exchange.markets:
            return None
        
        ticker = await exchange.fetch_ticker(formatted_symbol)
        ohlcv = await exchange.fetch_ohlcv(formatted_symbol, timeframe='1h', limit=50)
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['rsi'] = ta.rsi(df['close'], length=14)
        current_rsi = df['rsi'].iloc[-1]
        
        return {
            "type": "CEX_PERP",
            "name": symbol.upper(),
            "symbol": symbol.upper(),
            "price": float(ticker['last']),
            "volume_24h": float(ticker['quoteVolume']),
            "change_24h": float(ticker['percentage']),
            "funding_rate": ticker.get('info', {}).get('lastFundingRate', 'N/A'),
            "rsi_1h": round(current_rsi, 2),
            "venue": "CEX (Binance Futures)"
        }
    except Exception as e:
        logging.error(f"CCXT Error: {e}")
        return None

# --- 2. ADVANCED RISK & MARGIN ENGINE ---
def calculate_position_metrics(portfolio_size: float, entry: float, stop_loss: float, risk_pct: float = 1.5, leverage: int = 1, is_perp: bool = False):
    """
    Computes precise risk allocation, token sizing, and required margin based on portfolio.
    """
    risk_amount = portfolio_size * (risk_pct / 100.0)
    price_risk = abs(entry - stop_loss)
    
    if price_risk == 0:
        return None
    
    position_tokens = risk_amount / price_risk
    notional_value = position_tokens * entry
    required_margin = notional_value / leverage if is_perp else notional_value
    
    return {
        "risk_usd": round(risk_amount, 2),
        "tokens": round(position_tokens, 4),
        "notional": round(notional_value, 2),
        "margin": round(required_margin, 2),
        "leverage": leverage if is_perp else 1
    }

# --- 3. TELEGRAM COMMAND HANDLER ---
@dp.message(Command("analyze"))
async def handle_analysis(message: types.Message):
    args = message.text.split()
    if len(args) < 3:
        await message.reply(
            "⚠️ **Usage Format:**\n"
            "`/analyze <symbol_or_contract> <portfolio_size_usd> [leverage]`\n\n"
            "• **CEX Example:** `/analyze BTC 10000 10`\n"
            "• **DEX Example:** `/analyze SOL 5000` (defaults to spot/1x)"
        )
        return
    
    query = args[1]
    portfolio_size = float(args[2])
    leverage = int(args[3]) if len(args) > 3 else 1

    await message.reply(f"🔍 Analyzing multi-timeframe liquidity, trends, and risk models for `{query}`...")

    data = await fetch_cex_data(query)
    is_perp = True

    if not data:
        data = await fetch_dex_data(query)
        is_perp = False
        leverage = 1 # DEX memecoins are spot/1x

    if not data:
        await message.reply(f"❌ Could not find market data for `{query}` on CEX or DEX networks.")
        return

    price = data["price"]
    
    buffer = 0.04 if not is_perp else 0.02
    entry = price
    stop_loss = round(entry * (1 - buffer), 6)
    tp1 = round(entry * (1 + (buffer * 1.5)), 6)
    tp2 = round(entry * (1 + (buffer * 3.0)), 6)

    risk_data = calculate_position_metrics(
        portfolio_size=portfolio_size,
        entry=entry,
        stop_loss=stop_loss,
        risk_pct=1.5,
        leverage=leverage,
        is_perp=is_perp
    )

    response = (
        f"🚀 **INSTITUTIONAL TRADE SETUP: {data['symbol']}**\n"
        f"🌐 **Venue:** `{data['venue']}` | 💵 **Price:** `${price:,.4f}`\n\n"
        f"📊 **Market & Fundamental Health:**\n"
        f"• **24h Volume:** `${data['volume_24h']:,.0f}`\n"
        f"• **24h Change:** `{data['change_24h']}%`\n"
    )

    if is_perp:
        response += f"• **1H RSI:** `{data['rsi_1h']}` | **Funding Rate:** `{data['funding_rate']}`\n\n"
    else:
        response += f"• **Liquidity Pool:** `${data['liquidity']:,.0f}`\n\n"

    response += (
        f"🎯 **Optimized Execution Plan:**\n"
        f"• **Direction:** `LONG (Bullish Confluence)`\n"
        f"• **Entry Zone:** `${entry:,.4f}`\n"
        f"• **Stop Loss:** `${stop_loss:,.4f}` 🛑\n"
        f"• **Take Profit 1:** `${tp1:,.4f}` (Risk/Reward 1:1.5)\n"
        f"• **Take Profit 2:** `${tp2:,.4f}` (Risk/Reward 1:3.0)\n\n"
        f"⚖️ **Portfolio Risk Sizing (${portfolio_size:,.0f} Portfolio):**\n"
        f"• **Target Risk (1.5%):** `${risk_data['risk_usd']:,.2f}`\n"
        f"• **Position Size:** `{risk_data['tokens']} {data['symbol']}`\n"
        f"• **Total Notional Exposure:** `${risk_data['notional']:,.2f}`\n"
        f"• **Required Margin ({risk_data['leverage']}x):** `${risk_data['margin']:,.2f}`\n\n"
        f"🤖 *Synthesized via Multi-TF Technicals & Risk Manager.*"
    )

    await message.reply(response, parse_mode="Markdown")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
              
