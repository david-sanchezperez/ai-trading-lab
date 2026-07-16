"""
Configuración central del proyecto.
Todas las rutas se definen aquí usando pathlib para que el proyecto
funcione en cualquier máquina independientemente de dónde esté instalado.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------

# Raíz del proyecto: sube dos niveles desde core/config.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Directorios de datos
DATA_DIR    = PROJECT_ROOT / "data" / "raw"
CHROMA_DIR  = PROJECT_ROOT / "data" / "chromadb"
LOGS_DIR    = PROJECT_ROOT / "logs"

# Archivos clave
DAILY_SIGNALS_PATH      = LOGS_DIR / "daily_signals.json"
PNL_HISTORY_PATH        = LOGS_DIR / "pnl_history.json"
PORTFOLIO_SIM_PATH      = PROJECT_ROOT / "data" / "portfolio_sim.json"
ENTRY_POINTS_PATH       = PROJECT_ROOT / "data" / "entry_points.json"

# ---------------------------------------------------------------------------
# Scheduler — franjas horarias (timezone Europe/Madrid = CET/CEST)
# ---------------------------------------------------------------------------

TIMEZONE         = "Europe/Madrid"
PRE_MARKET_TIME        = "13:00"   # descarga datos + actualización RAG
POST_MARKET_TIME       = "20:30"   # análisis completo + resumen (cierre NYSE ~22:00 CET, ~1h margen)
TRAILING_STOP_TIME     = "21:15"   # trailing stops + take profits (tras post_market)

# ---------------------------------------------------------------------------
# Inferencia LLM — critic agent
# ---------------------------------------------------------------------------

# llama.cpp-tq3 con Qwen3.6-35B-A3B TurboQuant, API OpenAI-compatible
CRITIC_LLM_URL   = "http://localhost:8080/v1/chat/completions"
CRITIC_LLM_MODEL = "qwen3.6-35b-a3b"   # nombre que expone llama-server

# Earnings: no operar en los N días antes/después de la publicación
EARNINGS_BUFFER_DAYS = 2

# ---------------------------------------------------------------------------
# Mercado
# ---------------------------------------------------------------------------

MARKET_CALENDAR = "NYSE"     # para pandas-market-calendars

# ---------------------------------------------------------------------------
# APIs externas
# ---------------------------------------------------------------------------

# SEC EDGAR requiere identificación para sus APIs (Fair Access Policy).
# edgartools la usa en cada solicitud. Se carga desde variable de entorno
# para no hardcodear un email personal en el repo público.
EDGAR_IDENTITY = os.environ.get("EDGAR_IDENTITY", "your-email@example.com")

# ---------------------------------------------------------------------------
# Utilidad: garantizar que los directorios existen
# ---------------------------------------------------------------------------

def ensure_dirs():
    """Crea los directorios necesarios si no existen."""
    cache_dir = PROJECT_ROOT / "data" / "cache"
    for d in [DATA_DIR, CHROMA_DIR, LOGS_DIR, cache_dir]:
        d.mkdir(parents=True, exist_ok=True)
