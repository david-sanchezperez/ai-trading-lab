import pandas as pd
from core.indicators import add_indicators

df = pd.read_csv("data/raw/NVDA.csv")

df = add_indicators(df)

print(df.tail(5))
