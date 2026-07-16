#!/bin/bash
set -e

echo "=== AI Trading Lab — Instalar servicios systemd ==="
echo ""

# ── 1. Generar .env desde .envrc (elimina 'export ') ─────────────────────────
if [ ! -f ".envrc" ]; then
    echo "ERROR: .envrc no encontrado en $(pwd)"
    exit 1
fi

grep '^export ' .envrc | sed 's/^export //' > .env
echo "[1/4] .env generado desde .envrc"

# ── 2. Copiar services al directorio de usuario ───────────────────────────────
mkdir -p ~/.config/systemd/user/
cp systemd/trading-scheduler.service ~/.config/systemd/user/
cp systemd/trading-bot.service ~/.config/systemd/user/
echo "[2/4] Services copiados a ~/.config/systemd/user/"

# ── 3. Recargar daemon y habilitar services ───────────────────────────────────
systemctl --user daemon-reload
systemctl --user enable trading-scheduler.service trading-bot.service
echo "[3/4] Services habilitados (arrancarán al login)"

# ── 4. Instrucciones ──────────────────────────────────────────────────────────
echo "[4/4] Listo"
echo ""
echo "=== Comandos útiles ==="
echo "  Arrancar ahora:    systemctl --user start trading-scheduler trading-bot"
echo "  Estado:            systemctl --user status trading-scheduler trading-bot"
echo "  Logs scheduler:    journalctl --user -u trading-scheduler -f"
echo "  Logs bot:          journalctl --user -u trading-bot -f"
echo "  Deshabilitar:      systemctl --user disable trading-scheduler trading-bot"
