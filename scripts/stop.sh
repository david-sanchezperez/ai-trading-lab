#!/bin/bash

PIDS_FILE=".pids"

echo "=== AI Trading Lab — Shutdown ==="
echo ""

if [ ! -f "$PIDS_FILE" ]; then
    echo "ERROR: No se encontró $PIDS_FILE"
    echo "       El sistema puede no estar corriendo o fue iniciado manualmente."
    exit 1
fi

echo "Leyendo PIDs desde $PIDS_FILE..."
echo ""

while IFS='=' read -r name pid; do
    if [ -z "$pid" ]; then
        continue
    fi

    if kill -0 "$pid" 2>/dev/null; then
        echo "  Deteniendo $name (PID $pid)..."
        kill "$pid"
        echo "  $name detenido."
    else
        echo "  $name (PID $pid) ya no estaba corriendo."
    fi
done < "$PIDS_FILE"

rm -f "$PIDS_FILE"
echo ""
echo "=== Sistema detenido ==="
