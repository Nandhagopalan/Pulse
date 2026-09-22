# The Swing Book in Stock Options — Backtest

**Status:** a research result. It changes nothing in the live book.
**Reproduce:** [`scripts/swing_options.py`](../scripts/swing_options.py) (§5).
**Data:** stock futures and options from the NSE F&O bhavcopy (`pulse-terminal-fno`, raw layer), and the share book's bars (`pulse-terminal`).
**Calibration window:** 2011-01-03 → 2022-12-30. **Test window:** 2024-01-01 → 2026-09-18, never used to choose anything.

The question was whether the swing strategy (breakouts in strong stocks, a market
regime switch, ATR stops; [strategy-engine.md](strategy-engine.md)) can be run
through F&O instead of shares, and whether doing so improves it.

---

## The answer

**No. Keep the book in shares.** Neither of the two routes into F&O improved it:

1. **Trading only F&O stocks roughly halves the return.** The strategy earns most
   of its edge in mid-caps that have no derivatives.
2. **Carrying the F&O-eligible trades in calls or call spreads instead of shares
   did not help either.** Of 160 option variants, the 19 that beat the share
   book's 2011–2022 CAGR did so by at most 0.2 points, each carrying 48 or fewer
   trades in options, and none passed the selection rule set before the test was read. The variants
   that looked better in 2024–2026 did so by holding *less* of trades that lost.
   Skipping those trades outright did the same thing more cheaply.

---

## 1. Only about 30% of swing trades can use F&O at all

The F&O segment lists 150–250 stocks, depending on the year. Of the swing book's
trades from 2011 to 2026, **29.8% were in a stock with listed F&O on the entry
date**, and those trades returned less per trade than the rest (2.9% against
4.1%, win rate 39% against 44%, `deployed` preset).

Restricting the same rules to F&O stocks, so that the free slots fill with F&O
signals instead:

| Book, 2011–2026 | CAGR | Max drawdown | 2024–26 CAGR |
| --- | --- | --- | --- |
| `deployed`, all stocks (as it runs) | 18.6% | −24.7% | 19.2% |
| `deployed`, F&O stocks only | 10.8% | −29.4% | 12.3% |
| `balanced`, all stocks | 14.5% | −15.8% | 12.3% |
| `balanced`, F&O stocks only (momentum ranked within F&O) | 10.2% | −14.5% | 6.6% |

Futures instead of shares do not change this. A future's price already includes
the cost of carry, and the cash its margin frees earns about the same back; what
is left is leverage. The book is also not short of capital: across 15 years it
passed on 60 signals for lack of cash.

## 2. Calls instead of shares on the F&O trades

The share book runs exactly as it trades. Each trade whose stock had options on
the entry date is instead carried by an option position. The stock still decides
when to enter and exit, and every other trade stays in shares.

What was varied (80 configurations per preset):

- **Instrument:** a single call, or a 10%-wide bull call spread.
- **Strike:** from 10% in the money to 5% out of the money.
- **Expiry:** the first monthly expiry at least 20 or 45 days out, rolled 5 days before expiry.
- **Size:** either the same rupee exposure as the shares (×1 or ×1.5), or a premium of 0.5, 1 or 2 times the trade's risk budget.

The best-performing plain versions next to the controls:

| `deployed` | Trades carried | 2011–22 CAGR | Max DD | 2024–26 CAGR | Max DD |
| --- | --- | --- | --- | --- | --- |
| **Shares, as the book trades** | — | **14.49%** | −24.66% | **19.15%** | −22.14% |
| Shares at the options' closes (timing control) | 288 | 14.02% | −24.79% | 20.08% | −21.87% |
| F&O trades not taken | 288 | 10.55% | −20.87% | 12.96% | −14.93% |
| Call 5% ITM, same exposure | 97 | 13.91% | −26.73% | 19.30% | −21.83% |
| Call at the money, same exposure | 142 | 13.40% | −27.17% | 15.49% | −20.13% |
| Call 5% OTM, same exposure | 135 | 13.64% | −27.53% | 17.27% | −19.06% |
| Bull call spread at the money | 103 | 13.93% | −26.67% | 17.26% | −17.75% |

| `balanced` | Trades carried | 2011–22 CAGR | Max DD | 2024–26 CAGR | Max DD |
| --- | --- | --- | --- | --- | --- |
| **Shares, as the book trades** | — | **13.30%** | −15.84% | 12.32% | −10.43% |
| Shares at the options' closes (timing control) | 273 | 13.44% | −14.89% | 11.39% | −10.71% |
| F&O trades not taken | 273 | 12.15% | −13.94% | **15.34%** | **−7.63%** |
| Call 5% ITM, same exposure | 94 | 13.14% | −16.27% | 13.52% | −9.96% |
| Call at the money, same exposure | 147 | 12.71% | −15.53% | 13.20% | −9.06% |
| Call 5% OTM, same exposure | 139 | 13.22% | −15.29% | 14.13% | −8.58% |
| Bull call spread at the money | 102 | 13.10% | −16.37% | 14.86% | −9.95% |

**The selection rule.** It was written into the script before the test was read:

- **Eligible:** at least 100 trades carried in options, and no sub-period of 2011–14, 2015–18 or 2019–22 below the share book.
- **Pick:** the eligible config with the highest Calmar.

**No configuration passed, in either preset.** Across the grid, calibration rank
predicted little of test rank (rank correlation +0.33 for `deployed`, −0.03 for
`balanced`).

Why the options lose:

- **An option pays for time, and these trades are often wrong.** The book wins
  43–46% of trades and makes its money on a few long runs. A call loses its time
  value on every trade that goes nowhere and must be rolled monthly on the ones
  that run. In `balanced`, 5%-out-of-the-money calls won 20% of the trades they
  carried, where the shares won 34% of the same trades.
- **Stock options are thin away from the money and beyond the near month.**
  Once a strike needed 25 contracts that session to count as tradeable, only
  about half the eligible trades found one near an at-the-money target, and about
  a third near a 5%-in-the-money one. Deep in-the-money
  legs, the ones that behave most like the stock, were the least liquid.
- **Closing a thin leg is expensive.** One BSE spread made ₹4.7 lakh by mid-May
  2026 and gave ₹3.8 lakh of it back in spread costs when closed near expiry.

**What the 2024–26 "improvements" are.** The better `balanced` rows in the test
window hold these trades with a small out-of-the-money call, about 2% of the
position's value in premium. That caps the loss on trades that went nowhere, and
those trades lost heavily in 2025. The row that simply skips them did better
still (15.34%), and skipping them cost 1.2 points a year over 2011–2022. This is
a question of whether to take F&O-stock trades, not an options edge, and one
good test period for skipping them is not enough evidence to change the book.

## 3. How the test was kept honest

- **The share P&L being replaced is the engine's own.** It is rebuilt per trade
  from the engine's fills and matches its `pnl` to the rupee on all 2,137 trades.
  With nothing substituted, the hybrid reproduces both books' daily returns
  exactly (`smoke`).
- **Traded, liquid prices only.**
  - Options are bought, sold and rolled only at a strike with at least 25 contracts traded that session.
  - Other strikes are marked by Black-76 at the smile of the liquid strikes, priced against the same expiry's future.
  - A strike that traded 1 or 2 contracts is not a price. On 2024-12-18, BSE's January 5500 call closed above its 5400 call.
- **Costs.**
  - Fills pay a half-spread of 1.5% of premium, tripled on an illiquid strike.
  - Charges: STT on option sales (0.0625%, then 0.1% from Oct 2024), exchange, SEBI, GST, stamp duty, and ₹20 per order.
  - Stress runs at 2× and 3× spreads move the results down, never up.
- **Corporate actions.**
  - A bonus or split shows as the raw future and the adjusted close parting by over 15% in a day.
  - Open legs are then rescaled the way NSE rescales them.
  - Before this was handled, a 2:1 BSE bonus read as an almost total loss.
- **A roll that finds no liquid strike switches the trade into shares** for the rest of its life, rather than leaving it flat.
- **Timing.**
  - Options have only end-of-day prices, so they trade at the close of the session the shares fill at the open.
  - The "shares at the options' closes" row isolates that difference. It is small (−0.5 to +0.1 points in calibration).
- **Selection before the test.** The rule was fixed in the script's docstring before the test window was read.

## 4. What F&O is still good for here

This study closes the "run the swing book through options" idea. It does not
rule out using F&O *around* the book:

- **The unhedged regime exit is the thing a NIFTY hedge would replace.** When the
  regime turns off, the book sells everything at the next open. A NIFTY put or a
  short NIFTY future could be tested as a cheaper way to take that risk off.
  That is a separate study, and the regime exit already works.
- **Whether to take F&O-stock trades at all** is now a live question for
  `balanced` (§2). It belongs in the swing research (`scripts/walkforward.py`
  harness), not here.

## 5. Reproduce

```bash
# 1. The raw F&O files must be in the bucket (python -m pipeline fno ..., start each year at Jan 1)
# 2. Stock futures, and stock options that traded, to a local mirror
uv run python -m scripts.swing_options extract --out DIR --years 2011-2026
# 3. The share book's trades (PULSE_BARS_PKL is optional, a pickled MarketData to skip the R2 read)
FNO_MIRROR=DIR uv run python -m scripts.swing_options trades
# 4. Accounting checks, then the grid, then the report
FNO_MIRROR=DIR uv run python -m scripts.swing_options smoke
FNO_MIRROR=DIR uv run python -m scripts.swing_options sweep
FNO_MIRROR=DIR uv run python -m scripts.swing_options analyse
```

The F&O-only universe runs in §1 were made in the working session with the
engine's own universe mask narrowed to stocks with listed futures each month.
They are not yet a script.
