"""
Job post-market: se ejecuta a las 20:45 CET al cierre de NYSE.
Secuencia: verificar calendario → ejecutar análisis → enviar resumen con señales.
"""

import json
import logging
from datetime import date

import pandas_market_calendars as mcal

from core.config import DAILY_SIGNALS_PATH, MARKET_CALENDAR
from scheduler.notifier import send_notification

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def _is_market_open_today() -> bool:
    cal = mcal.get_calendar(MARKET_CALENDAR)
    today = date.today().isoformat()
    schedule = cal.schedule(start_date=today, end_date=today)
    return not schedule.empty


def _build_summary_message(data: dict) -> str:
    summary = data.get("summary", {})
    buy  = summary.get("BUY",  [])
    sell = summary.get("SELL", [])
    hold = summary.get("HOLD", [])
    fecha = data.get("date", "—")
    regime = data.get("regime_adjustment", 1.0)
    threshold = data.get("effective_threshold", 0.70)

    signals = data.get("signals", [])
    actionable = [s for s in signals if s.get("decision") in ("BUY", "SELL")]
    actionable.sort(key=lambda s: abs(s.get("score", 0)), reverse=True)

    lines = [
        f"📊 *Señales del día — {fecha}*",
        "",
        f"🟢 *BUY ({len(buy)}):* {', '.join(buy) or '—'}",
        f"🔴 *SELL ({len(sell)}):* {', '.join(sell) or '—'}",
        f"⚪ *HOLD:* {len(hold)} tickers",
    ]

    if actionable:
        lines.append("")
        lines.append("*Top señales:*")
        for s in actionable[:5]:
            icon = "🟢" if s["decision"] == "BUY" else "🔴"
            fast = " ⚡" if s.get("critic_verdict") == "APPROVED" and s.get("score") else ""
            lines.append(
                f"{icon} {s['ticker']} — score {s['score']:+.3f} | "
                f"RSI {s['rsi']:.0f} | ${s['price']:.2f}{fast}"
            )

    strong_holds = [
        s for s in signals
        if s.get("decision") == "HOLD" and abs(s.get("score", 0)) > 0.6
    ]
    strong_holds.sort(key=lambda s: abs(s.get("score", 0)), reverse=True)
    if strong_holds:
        lines.append("")
        lines.append("*Señales fuertes bloqueadas (HOLD):*")
        for s in strong_holds[:3]:
            lines.append(
                f"↳ {s['ticker']} score {s['score']:+.3f} | "
                f"{s.get('critic_verdict', '?')} | RSI {s['rsi']:.0f}"
            )

    lines += [
        "",
        f"📈 Régimen: `{regime:.3f}x` | Umbral efectivo: `{threshold:.3f}`",
        f"Analizados: {data.get('tickers_analyzed', 0)} | Errores: {len(data.get('errors', []))}",
    ]
    return "\n".join(lines)


def run():
    log.info("[post_market] Iniciando job post-market")

    # ── Paso 1: verificar calendario NYSE ────────────────────────────────────
    try:
        if not _is_market_open_today():
            msg = f"📅 Hoy ({date.today()}) NYSE no abrió. Job post-market cancelado."
            log.info(f"[post_market] {msg}")
            send_notification(msg)
            return
        log.info("[post_market] NYSE abrió hoy — continuando")
    except Exception as e:
        log.warning(f"[post_market] Error verificando calendario: {e} — continuando igualmente")

    # ── Paso 2: ejecutar análisis completo ───────────────────────────────────
    try:
        log.info("[post_market] Ejecutando análisis de tickers...")
        from scripts.analyze_all import main as analyze_main
        analyze_main()
        log.info("[post_market] Análisis completado")
    except Exception as e:
        msg = f"❌ *post_market* — Error en analyze_all: `{e}`"
        log.error(f"[post_market] {msg}")
        send_notification(msg)
        return

    # ── Paso 3: cargar resultados y enviar resumen ────────────────────────────
    try:
        if not DAILY_SIGNALS_PATH.exists():
            log.error("[post_market] daily_signals.json no encontrado tras analyze_all")
            send_notification("❌ *post_market* — daily_signals.json no generado.")
            return

        with open(DAILY_SIGNALS_PATH, "r") as f:
            data = json.load(f)

        msg = _build_summary_message(data)
        ok = send_notification(msg)
        if ok:
            log.info("[post_market] Resumen enviado correctamente")
        else:
            log.warning("[post_market] No se pudo enviar el resumen por Telegram")
    except Exception as e:
        log.error(f"[post_market] Error construyendo/enviando resumen: {e}")

    log.info("[post_market] Job completado")


if __name__ == "__main__":
    run()
