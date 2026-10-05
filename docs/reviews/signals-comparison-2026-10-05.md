# Signals comparison: classic vs vol-normalised technical

Date 2026-10-05; 86 symbols in `history`; mode `top`; profiles balanced; history provenance: real.

Research only. Both modes use the same weights and risk rules; `technical_mode` stays `classic` by default and no gate was recorded.

| Profile | Split | Mode | Runs | Success | Mean return % | Mean excess % | Mean max DD % |
|---|---|---|---|---|---|---|---|
| balanced | train | classic | 82 | 2% | -0.1 | -5.6 | 5.6 |
| balanced | train | vol_normalized | 82 | 1% | -0.6 | -6.1 | 5.6 |
| balanced | heldout | classic | 29 | 7% | 1.8 | -7.9 | 5.1 |
| balanced | heldout | vol_normalized | 29 | 10% | 2.4 | -7.3 | 5.0 |

Notes: the universe is today's names (survivorship bias); few independent windows mean wide uncertainty; the analyst revisions variant has no offline history and is not covered. Not financial advice.
