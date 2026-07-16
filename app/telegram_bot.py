"""
Telegram bot para AI Trading Lab.
Comandos:
  /analyze TICKER  — ejecuta el grafo completo y envía el resultado
  /sentiment TICKER — solo sentimiento FinBERT
  /help            — muestra ayuda
"""

import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from graph.trading_graph import build_graph, TradingState

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_analysis(result: dict) -> str:
    tech = result["technical_result"] or {}
    sent = result["sentiment_result"] or {}
    critic = result["critic_result"] or {}
    decision = result["decision"] or {}
    execution = result["execution_result"] or {}
    portfolio = execution.get("portfolio", {})

    headlines_block = ""
    for r in (sent.get("raw_results") or [])[:3]:
        emoji = "🟢" if r["score"] > 0 else ("🔴" if r["score"] < 0 else "⚪")
        headlines_block += f"  {emoji} {r['title'][:55]}\n"

    verdict_emoji = "✅" if critic.get("verdict") == "APPROVED" else "⚠️"

    lines = [
        f"📊 *{result['ticker']} Analysis*",
        "",
        f"*Technical*",
        f"  Signal: `{tech.get('signal')}` ({tech.get('confidence', 0):.0%} confidence)",
        f"  RSI: `{tech.get('rsi', 0):.1f}` | Price: `${tech.get('price', 0):.2f}`",
        "",
        f"*Sentiment* — {sent.get('headlines', 0)} headlines · `{sent.get('sentiment', 0):+.4f}`",
        headlines_block.rstrip(),
        "",
        f"*Critic* {verdict_emoji} `{critic.get('verdict')}`",
        f"  Threshold: `{decision.get('threshold_used', 0.7)}`"
        + (" _(critic override)_" if decision.get("critic_override") else ""),
        "",
        f"*Decision*: `{decision.get('action')}` (score: `{decision.get('score', 0):+.3f}`)",
        "",
        f"*Portfolio*",
        f"  Cash: `${portfolio.get('cash', 0):,.2f}`",
        f"  Total value: `${portfolio.get('total_value', 0):,.2f}`",
        f"  Positions: `{len(portfolio.get('positions', {}))}`",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *AI Trading Lab Bot*\n\n"
        "/analyze `TICKER` — análisis completo del grafo\n"
        "/sentiment `TICKER` — solo sentimiento FinBERT\n"
        "/help — esta ayuda"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /analyze TICKER (ej. /analyze NVDA)")
        return

    ticker = context.args[0].upper()
    await update.message.reply_text(f"⏳ Analizando {ticker}... (puede tardar ~30s)")

    try:
        app_graph = build_graph()
        initial_state: TradingState = {
            "ticker": ticker,
            "df": None,
            "technical_result": None,
            "sentiment_result": None,
            "critic_result": None,
            "decision": None,
            "execution_result": None,
            "portfolio": None,
        }

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, app_graph.invoke, initial_state)

        text = format_analysis(result)
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error analizando {ticker}: {e}")


async def cmd_sentiment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /sentiment TICKER (ej. /sentiment NVDA)")
        return

    ticker = context.args[0].upper()
    await update.message.reply_text(f"⏳ Obteniendo sentimiento para {ticker}...")

    try:
        from core.news_fetcher import get_ticker_sentiment
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, get_ticker_sentiment, ticker)

        lines = [f"📰 *{ticker} Sentiment*\n"]
        lines.append(f"Score: `{result['sentiment']:+.4f}` | Headlines: `{result['headlines']}`\n")
        for r in result["raw_results"][:5]:
            emoji = "🟢" if r["score"] > 0 else ("🔴" if r["score"] < 0 else "⚪")
            lines.append(f"{emoji} `{r['score']:+.2f}` {r['title'][:55]}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("sentiment", cmd_sentiment))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
