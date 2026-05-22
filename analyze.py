import pandas as pd

df = pd.read_csv('output/backtest_results.csv')

print("=== BY DIRECTION ===")
print(f"{'Direction':<8} | {'Trades':>6} | {'Win Rate':>8} | {'Avg PnL%':>9} | {'Total PnL%':>10} | {'PF':>6}")
print("-" * 60)
for d in ['BULL', 'MIXED', 'BEAR']:
    sub = df[df.direction == d]
    if len(sub) == 0:
        continue
    wr = (sub.r_multiple > 0).mean() * 100
    gw = sub[sub.pnl_pct > 0].pnl_pct.sum()
    gl = abs(sub[sub.pnl_pct <= 0].pnl_pct.sum())
    pf = gw / max(gl, 0.01)
    print(f"{d:<8} | {len(sub):>6} | {wr:>7.1f}% | {sub.pnl_pct.mean():>+8.1f}% | {sub.pnl_pct.sum():>+9.1f}% | {pf:>6.2f}")

print()
print("=== BULL - BY SCORE BUCKET ===")
bull = df[df.direction == 'BULL']
print(f"{'Score':<8} | {'Trades':>6} | {'Win Rate':>8} | {'Avg PnL%':>9} | {'Total PnL%':>10} | {'PF':>6}")
print("-" * 60)
for lo, hi in [(0, 20), (20, 40), (40, 60), (60, 100)]:
    sub = bull[(bull.score >= lo) & (bull.score < hi)]
    if len(sub) == 0:
        continue
    wr = (sub.r_multiple > 0).mean() * 100
    gw = sub[sub.pnl_pct > 0].pnl_pct.sum()
    gl = abs(sub[sub.pnl_pct <= 0].pnl_pct.sum())
    pf = gw / max(gl, 0.01)
    print(f"{lo}-{hi:<6} | {len(sub):>6} | {wr:>7.1f}% | {sub.pnl_pct.mean():>+8.1f}% | {sub.pnl_pct.sum():>+9.1f}% | {pf:>6.2f}")

print()
print("=== TOP TICKERS - BULL (min 3 trades, sorted by Total PnL%) ===")
print(f"{'Ticker':<7} | {'Trades':>6} | {'Win Rate':>8} | {'Avg PnL%':>9} | {'Total PnL%':>10} | {'Best':>7} | {'Worst':>7}")
print("-" * 70)
results = []
for ticker in bull.ticker.unique():
    sub = bull[bull.ticker == ticker]
    if len(sub) < 3:
        continue
    wr = (sub.r_multiple > 0).mean() * 100
    results.append((ticker, len(sub), wr, sub.pnl_pct.mean(), sub.pnl_pct.sum(), sub.pnl_pct.max(), sub.pnl_pct.min()))
for r in sorted(results, key=lambda x: x[4], reverse=True):
    print(f"{r[0]:<7} | {r[1]:>6} | {r[2]:>7.1f}% | {r[3]:>+8.1f}% | {r[4]:>+9.1f}% | {r[5]:>+6.1f}% | {r[6]:>+6.1f}%")
