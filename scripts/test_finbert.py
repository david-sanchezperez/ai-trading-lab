from transformers import pipeline

finbert = pipeline(
    "sentiment-analysis",
    model="ProsusAI/finbert",
    tokenizer="ProsusAI/finbert"
)

titulares = [
    "Nvidia reports record earnings driven by AI demand",
    "AMD faces headwinds as competition intensifies",
    "Markets rally on positive Fed signals",
]

for t in titulares:
    result = finbert(t)[0]
    print(f"{result['label']:10} {result['score']:.2f}  {t}")
