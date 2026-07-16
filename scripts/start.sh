#!/bin/bash
set -e

PIDS_FILE=".pids"

echo "=== AI Trading Lab — Startup ==="
echo ""

# ── Verificar venv ────────────────────────────────────────────────────────────
if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: venv no encontrado en .venv/"
    echo "       Crea el entorno con: python -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

echo "[1/4] Activando entorno y variables..."
source .venv/bin/activate
source .envrc
echo "      OK"

# ── Garantizar directorios ────────────────────────────────────────────────────
echo ""
echo "[2/4] Verificando directorios..."
python -c "from core.config import ensure_dirs; ensure_dirs()"
echo "      OK"

# ── Lanzar bot de Telegram ────────────────────────────────────────────────────
echo ""
echo "[3/4] Arrancando bot de Telegram..."
python -m app.telegram_bot &
BOT_PID=$!
echo "      Bot PID: $BOT_PID"

# ── Lanzar scheduler ──────────────────────────────────────────────────────────
echo ""
echo "[4/4] Arrancando scheduler (pre-market 13:00 / post-market 21:00 CET)..."
python -m scheduler.market_scheduler &
SCHEDULER_PID=$!
echo "      Scheduler PID: $SCHEDULER_PID"

# ── Guardar PIDs ──────────────────────────────────────────────────────────────
echo "bot=$BOT_PID" > "$PIDS_FILE"
echo "scheduler=$SCHEDULER_PID" >> "$PIDS_FILE"

echo ""
echo "=== Sistema activo ==="
echo "  Bot Telegram:  PID $BOT_PID"
echo "  Scheduler:     PID $SCHEDULER_PID"
echo "  PIDs guardados en: $PIDS_FILE"
echo ""
echo "  Para parar todo: bash scripts/stop.sh"
echo "  Para parar manualmente: kill $BOT_PID $SCHEDULER_PID"
echo ""

wait $BOT_PID $SCHEDULER_PID
