"""Walk-forward backtest of the deterministic score (technical + patterns) on stored history.

Nothing here calls a provider except `fetch-history` in the CLI. Everything the simulator knows on
day D comes from `HistoryStore.asof(symbol, D)`, which returns rows dated on or before D only.
"""
