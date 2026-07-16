import pandas as pd
from core.indicators import add_indicators

def main():
    df = pd.read_csv("data/raw/NVDA.csv")

    df = add_indicators(df)

    print("\nÚltimas filas con indicadores:\n")
    print(df.tail(5))


if __name__ == "__main__":
    main()
