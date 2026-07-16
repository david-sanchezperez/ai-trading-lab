import yfinance as yf
import pandas as pd
from core.config import DATA_DIR

TICKERS = {
    # ── CORE — Silicon ────────────────────────────────────────────────
    "AMD": {
        "thesis": "silicon",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "CPU/GPU para AI training e inference",
        "specific_risks": [
            "competitive pressure from NVIDIA custom solutions",
            "custom ASIC adoption reducing GPU demand",
        ],
    },
    "AVGO": {
        "thesis": "silicon",
        "role": "core",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Custom ASICs y networking silicon para hyperscalers",
        "specific_risks": [
            "customer concentration in top 3 hyperscalers",
        ],
    },
    "ASML": {
        "thesis": "silicon",
        "role": "core",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Monopolio EUV lithography — picks-and-shovels de todos los chips AI",
        "specific_risks": [
            "export restrictions to China",
            "Dutch government policy changes",
            "geopolitical semiconductor controls",
        ],
    },
    "TSM": {
        "thesis": "silicon",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Foundry líder mundial — fabrica chips de NVIDIA AMD AVGO MRVL",
        "specific_risks": [
            "Taiwan Strait geopolitical tensions",
            "US-China decoupling risk",
            "single geography concentration",
        ],
    },
    "MRVL": {
        "thesis": "silicon",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Custom ASICs (XPUs) para AI inference en hyperscalers",
        "specific_risks": [
            "customer concentration Amazon Google Microsoft",
            "SerDes technology execution risk",
        ],
    },
    # ── CORE — Infra / Power AI ───────────────────────────────────────
    "ANET": {
        "thesis": "infra_ai",
        "role": "core",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Networking switches para AI data centers — backbone de clusters GPU",
        "specific_risks": [
            "competition from Cisco Juniper",
            "customer concentration hyperscalers",
        ],
    },
    "VRT": {
        "thesis": "infra_ai",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Power y cooling para data centers AI — infraestructura física crítica",
        "specific_risks": [
            "supply chain constraints",
            "execution risk on rapid scaling",
        ],
    },
    "CEG": {
        "thesis": "infra_ai",
        "role": "core",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Nuclear energy para data centers AI — contratos con Microsoft y hyperscalers",
        "specific_risks": [
            "regulatory risk nuclear",
            "rate case outcomes",
            "policy changes clean energy",
        ],
    },
    "VST": {
        "thesis": "infra_ai",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Vistra Energy — nuclear + gas para data centers AI, contratos directos hyperscalers",
        "specific_risks": [
            "ERCOT price volatility",
            "regulatory risk utility Texas",
            "leverage from nuclear buildout capex",
        ],
    },
    # ── CORE — Platforms ──────────────────────────────────────────────
    "META": {
        "thesis": "platforms",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "AI features en redes sociales + Llama open source + AI hardware propio",
        "specific_risks": [
            "regulatory antitrust risk EU US",
            "advertising cycle sensitivity",
        ],
    },
    "ORCL": {
        "thesis": "platforms",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Oracle Cloud Infrastructure OCI — AI cloud compitiendo con AWS Azure",
        "specific_risks": [
            "negative free cash flow from capex buildout",
            "customer concentration OpenAI hyperscalers",
            "elevated long-term debt",
        ],
    },
    # ── STABILIZER ────────────────────────────────────────────────────
    "VEEV": {
        "thesis": "stabilizer",
        "role": "stabilizer",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Pharma SaaS — anchor de calibración, baja varianza, señales limpias",
        "specific_risks": [
            "competition from Salesforce Health Cloud",
        ],
    },
    "ISRG": {
        "thesis": "stabilizer",
        "role": "stabilizer",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Robótica quirúrgica con AI — calibración medtech, crecimiento predecible",
        "specific_risks": [
            "hospital capex cycle sensitivity",
        ],
    },
    "COST": {
        "thesis": "stabilizer",
        "role": "stabilizer",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Costco — modelo membership defensivo, señal de consumo macro, baja varianza",
        "specific_risks": [
            "margin compression from membership fee pressure",
            "monthly comps volatility vs quarterly earnings",
        ],
    },
    # ── EXPLORATION ───────────────────────────────────────────────────
    "CRWV": {
        "thesis": "infra_ai",
        "role": "exploration",
        "risk": "high",
        "type": "standard",
        "thesis_description": "Neocloud puro AI — vende compute GPU a OpenAI Meta Microsoft",
        "specific_risks": [
            "29B long-term debt",
            "negative free cash flow",
            "customer concentration",
        ],
    },
    "COHR": {
        "thesis": "infra_ai",
        "role": "exploration",
        "risk": "high",
        "type": "multibagger",
        "thesis_description": "Componentes ópticos para data centers AI — 800G y 1.6T interconnects",
        "specific_risks": [
            "execution risk scaling",
            "competition in optical components",
        ],
    },
    "MU": {
        "thesis": "silicon",
        "role": "exploration",
        "risk": "high",
        "type": "standard",
        "thesis_description": "HBM3e memory — cuello de botella de AI accelerators NVIDIA y AMD",
        "specific_risks": [
            "memory cycle volatility",
            "competition Samsung SK Hynix",
        ],
    },
    "CRM": {
        "thesis": "platforms",
        "role": "exploration",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Enterprise SaaS con AI Einstein — menos puro que ORCL pero cobertura mediática buena",
        "specific_risks": [
            "competition from Microsoft Copilot",
            "AI ROI skepticism in enterprise",
        ],
    },
    "APP": {
        "thesis": "platforms",
        "role": "exploration",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "AppLovin — AI-driven ad tech, AXON engine, earnings beats consistentes",
        "specific_risks": [
            "iOS/Android platform policy changes",
            "ad market cyclicality",
            "high revenue concentration in mobile gaming",
        ],
    },
    "AXON": {
        "thesis": "platforms",
        "role": "exploration",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Axon Enterprise — AI en seguridad pública, moat regulatorio y de datos",
        "specific_risks": [
            "government budget cycles",
            "policy risk law enforcement spending",
            "valuation premium vs growth rate",
        ],
    },
    "PANW": {
        "thesis": "platforms",
        "role": "exploration",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "Palo Alto Networks — AI cybersecurity platform, billings growth y platformization",
        "specific_risks": [
            "competition from CrowdStrike Microsoft Defender",
            "platformization transition compresses near-term billings",
        ],
    },
    # ── CONTEXT — no operables ────────────────────────────────────────
    "QQQ": {
        "thesis": "context",
        "role": "context",
        "risk": None,
        "type": "context",
        "thesis_description": "Proxy Nasdaq 100 — momentum tech para critic_threshold_adjustment",
        "specific_risks": [],
    },
    "SPY": {
        "thesis": "context",
        "role": "context",
        "risk": None,
        "type": "context",
        "thesis_description": "Proxy S&P 500 — régimen macro general",
        "specific_risks": [],
    },
    "VIX": {
        "thesis": "context",
        "role": "context",
        "risk": None,
        "type": "context",
        "thesis_description": "Volatilidad implícita — ajuste continuo de umbrales del Critic Agent",
        "specific_risks": [],
    },
    "US10Y": {
        "thesis": "context",
        "role": "context",
        "risk": None,
        "type": "context",
        "thesis_description": "Yield bono 10Y USA — presión sobre valoraciones growth y régimen risk-on/off",
        "specific_risks": [],
    },
}

# Todos los tickers operables (excluye context) — para análisis diario
TICKERS_FLAT = [t for t, m in TICKERS.items() if m["role"] != "context"]

# Solo core — para position sizing real
CORE_TICKERS = [t for t, m in TICKERS.items() if m["role"] == "core"]

# Solo context — para cargar features macro en el Critic Agent
CONTEXT_TICKERS = [t for t, m in TICKERS.items() if m["role"] == "context"]


def get_tickers_by_role(role: str) -> list[str]:
    return [t for t, m in TICKERS.items() if m["role"] == role]


def get_ticker_metadata(ticker: str) -> dict:
    if ticker in TICKERS:
        return {"ticker": ticker, **TICKERS[ticker]}
    return {
        "ticker": ticker,
        "thesis": "unknown",
        "role": "exploration",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "",
        "specific_risks": [],
    }


def fetch_data(ticker, period="6mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    df.reset_index(inplace=True)

    # Normalizar Date: elimina timezone y filas con fecha inválida o sin precio de cierre
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    df = df.dropna(subset=["Date", "Close"]).reset_index(drop=True)

    numeric_cols = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ticker"] = ticker

    return df


def save_data(df, ticker):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filename = DATA_DIR / f"{ticker}.csv"
    df.to_csv(filename, index=False)


def load_all_data(period="1y"):
    all_data = []
    failed = []

    for ticker in TICKERS_FLAT:
        print(f"Downloading {ticker}...")
        try:
            df = fetch_data(ticker, period=period)
            if df.empty:
                print(f"  WARNING: no data for {ticker}")
                failed.append(ticker)
                continue
            save_data(df, ticker)
            all_data.append(df)
        except Exception as e:
            print(f"  ERROR {ticker}: {e}")
            failed.append(ticker)

    if failed:
        print(f"\nFailed tickers: {failed}")

    return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()


if __name__ == "__main__":
    data = load_all_data(period="1y")
    print(f"\nTotal rows: {len(data)}")
    print(data[["ticker", "Date", "Close"]].tail())
