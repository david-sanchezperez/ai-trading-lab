import pandas as pd
from core.indicators import add_indicators
from agents.technical_agent import generate_signal

def main():
    df = pd.read_csv("data/raw/NVDA.csv")

    df = add_indicators(df)

    result = generate_signal(df)

    print("\nTechnical Agent Output:\n")
    print(result)

if __name__ == "__main__":
    main()
