"""
Configuración del monitor intraday.
"""

MONITOR_CONFIG = {
    "market_hours": {
        "open":     "15:30",
        "close":    "22:00",
        "timezone": "Europe/Madrid",
        "days":     ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    },
    "intervals": {
        "price_check_minutes":    20,
        "news_check_minutes":     30,
        "earnings_check_minutes": 15,
    },
    "defensive": {
        "price_move_atr_multiplier":  1.5,
        "news_confidence_threshold":  0.80,
        "volume_anomaly_multiplier":  3.0,
        "min_position_days":          1,
    },
    "opportunistic": {
        "min_score":                      1.30,
        "require_news_confirmation":      True,
        "max_intraday_entries_per_day":   2,
        "blackout_before_eod_minutes":    45,
    },
    "llm": {
        "model":       "qwen3.6-35b-a3b",
        "max_tokens":  1000,
        "temperature": 0.1,
    },
}
