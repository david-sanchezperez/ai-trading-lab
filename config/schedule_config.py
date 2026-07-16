"""
Configuración del scheduler. Complementa core/config.py para parámetros
específicos de timing y límites de ciclo.
"""

# Hora del ciclo principal (post-market, CET)
DAILY_RUN_TIME = "20:30"

# Timezone — debe coincidir con core/config.TIMEZONE
TIMEZONE = "Europe/Madrid"

# Número máximo de tickers por ciclo
MAX_TICKERS_PER_RUN = 21

# Timeout máximo por ticker (segundos) — el critic LLM puede tardar ~30s
PIPELINE_TIMEOUT_SECONDS = 300
