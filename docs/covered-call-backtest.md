# NIFTYBEES Covered Call — Backtest and Rules

**Status:** a research result, not a live strategy. Nothing here trades.
**Reproduce:** [`scripts/covered_call.py`](../scripts/covered_call.py) (§7).
**Data:** NIFTY monthly options from the NSE F&O bhavcopy (`pulse-terminal-fno`) and NIFTYBEES closes (`pulse-terminal`).
**Calibration window:** 2011-01-03 → 2022-12-30. **Test window:** 2024-01-01 → 2026-09-11, never used to choose anything.

A covered call holds NIFTYBEES and sells NIFTY calls against it every month. This
document records what 5,376 variants of that trade did — different strikes,
expiries, exits, adjustments, how much of the holding the calls cover, and eight
ways of hedging it, butterflies among them — and states the rules of the one that
held up.

---

## The answer

| | 2011–2022 CAGR | Max drawdown | 2024–2026 CAGR | Max drawdown |
| --- | --- | --- | --- | --- |
| Buy & hold NIFTYBEES | **10.15%** | −36.34% | 4.00% | −15.23% |
| **Put-protected covered call** (recommended, §1) | 8.22% | **−9.83%** | **6.57%** | **−6.20%** |
| Butterfly-hedged covered call (§2) | 9.28% | −25.94% | 6.72% | −12.33% |

- **In the 2011–2022 bull decade, no covered call beat simply holding NIFTYBEES by
  a margin worth having.** The best of 5,376 managed 10.46% against 10.15%, with
  the same crash exposure. Selling calls caps the rallies that made the decade.
- **What a covered call can do is keep most of the return with a fraction of the
  risk.** The recommended version gave up 1.9 points of CAGR a year and cut the
  worst fall from −36% to −10%. Per unit of drawdown it earned three times as much.
- **In the flatter 2024–2026 test it beat buy-and-hold outright:** 6.57% against
  4.00% a year, with its worst fall at −6.20% against −15.23%.
- **A butterfly is the wrong hedge for this trade.** It pays only between its
  wings, and a crash goes straight through them. The recommended version buys a
  plain put 5% below the market instead.

₹50 lakh became ₹1.29 crore over 2011–2022 in the recommended strategy (buy and
hold: ₹1.59 crore), and ₹59.3 lakh over 2024–2026 (buy and hold: ₹55.6 lakh).

---

## 1. The recommended strategy: put-protected covered call

### 1.1 The position

| Leg | Size | Detail |
| --- | --- | --- |
| Long NIFTYBEES | 94% of equity | The core holding. Pledged as margin collateral. |
| Cash | 6% of equity | Margin cash component, option debits, buybacks. |
| Short NIFTY calls | **Half** the holding's index exposure | Monthly expiry about 45 days out, strike **1% below** NIFTY (in the money). |
| Long NIFTY puts | **All** of the holding's index exposure | Same expiry, strike **5% below** NIFTY. |

*Index exposure* is the ETF's value divided by the NIFTY level: ₹47 lakh of
NIFTYBEES with NIFTY at 21,742 is 216 index units, so calls are written on 108
units and puts bought on 216.

### 1.2 Enter

Each cycle opens on the day the previous one expires, and the very first on day one.

1. **Choose the expiry.** Take the NIFTY monthly expiry whose days to expiry are
   closest to 45, and at least 7 days out. In practice this alternates between
   roughly 35 and 56 days; cycles average 41 days.
2. **Rebalance.** Buy or sell NIFTYBEES so it is 94% of total equity, with 6% in cash.
3. **Sell the calls.** On half the index exposure, at the strike nearest to 1%
   *below* NIFTY — among strikes that actually traded that session, and within 1%
   of that target. If none traded that close, wait a session.
4. **Buy the puts.** On the full index exposure, at the listed strike nearest to 5%
   below NIFTY, same expiry.

The call is sold in the money on purpose. It brings in several times the premium
of an out-of-the-money call, and that premium is what pays for the puts. Only the
time value is kept; the intrinsic part is given back as NIFTY stays above the strike.

### 1.3 Adjust

5. **Roll the call down after a 4% fall.** Check at each close. If NIFTY is 4% or
   more below its level when the current call was sold, and more than 2 days
   remain, buy the call back and sell a new one 1% below the *current* level, same
   expiry — only if the new strike is lower. The next 4% is measured from this sale.

That is the only adjustment. There is no action on rallies, and the puts are never
touched until expiry. Across 2011–2022 the roll-down fired 42 times in 106 cycles;
in 2024–2026, 5 times in 24.

### 1.4 Exit and roll

6. **Hold to expiry.** Every option settles in cash at intrinsic value against the
   expiry-day close. Short calls in the money pay out intrinsic; puts in the money
   pay intrinsic, less STT on exercise.
7. **Roll the same day.** On expiry, go straight back to step 1. Premium kept, put
   payoffs and ETF gains all compound through the step-2 rebalance.

### 1.5 Sizing and capital

The rules are proportional; real contracts come in lots. NIFTY's lot is 65 today.

- **One call lot needs two lots' worth of NIFTYBEES.** With NIFTY near 23,400 that
  is 2 × 65 × 23,400 ≈ ₹30.4 lakh, plus the 6% cash — **about ₹32 lakh as the
  practical minimum**, trading 1 call lot and 2 put lots.
- **Scale in pairs.** ₹64 lakh writes 2 call lots against 4 put lots; ₹96 lakh, 3 against 6.
- **Margin.** The short calls need margin, which pledged NIFTYBEES (after its
  haircut) covers. Exchange rules require part of it in cash-equivalents, which the
  6% buffer is for. Check your broker's pledge haircut and margin before sizing.

### 1.6 Worked example — the October 2024 fall, ₹50 lakh book

Real prices from the bhavcopy; lots rounded from the proportional sizes.

| Date | Action | Detail |
| --- | --- | --- |
| 2024-09-26 | Open | NIFTY 26,216. Hold 18,365 NIFTYBEES at ₹291.55 (₹53.5 lakh), cash ₹3.9 lakh. Expiry 31 Oct (35 days). |
| | Sell calls | 25950 CE, 102 index units (≈4 lots of 25) at ₹572.35 |
| | Buy puts | 24900 PE, 204 index units (≈8 lots of 25) at ₹62.20 |
| 2024-10-04 | Roll down | NIFTY 25,015, 4.6% below the sale. Buy back 25950 CE; sell 24750 CE at ₹623.50. |
| 2024-10-31 | Expire | NIFTY 24,205, **−7.67%** since entry. Calls +₹1,13,817, puts +₹1,28,895. |
| | Result | **Cycle −2.48%** against the index's −7.67%. The same day: rebalance, sell November. |

A quiet month looks like 2024-01-01 → 2024-02-29: NIFTY +1.11%, the 21500 CE sold
at ₹814.45 kept ₹35,273 net, the 20650 PE cost ₹25,136, and the cycle made +1.28%.

### 1.7 Results

| | 2011–2022 | 2024–2026 |
| --- | --- | --- |
| CAGR | 8.22% | 6.57% |
| Buy & hold CAGR | 10.15% | 4.00% |
| Max drawdown | −9.83% (Oct 2021 → Jun 2022) | −6.20% (Sep 2024 → Mar 2025, recovered Sep 2025) |
| Buy & hold max drawdown | −36.34% (Jan → Mar 2020) | −15.23% (Sep 2024 → Mar 2025) |
| Volatility | 7.39% | 6.11% |
| CAGR ÷ max drawdown | 0.84 (buy & hold 0.28) | 1.06 (buy & hold 0.26) |
| **ROA** — average annual profit ÷ average capital | **8.06%** | **6.20%** |
| Written yield — call premium sold a year ÷ ETF value | 19.27% | 15.18% |
| Call leg, net a year ÷ ETF value | −2.84% | +2.01% |
| Put leg, net a year ÷ ETF value | −0.90% | +0.54% |
| Costs a year (charges + spreads) ÷ equity | 0.23% | 0.18% |
| Cycles / average length | 106 / 41 days | 24 / 41 days |
| Cycles that made money | 67.9% | 70.8% |
| Average / worst / best cycle | +0.93% / −3.89% / +7.83% | +0.73% / −2.48% / +5.12% |
| Sub-periods 2011–14 / 2015–18 / 2019–22 | 7.64% / 8.60% / 8.43% | — |
| ₹50 lakh became | ₹1.29 crore (buy & hold ₹1.59 crore) | ₹59.3 lakh (buy & hold ₹55.6 lakh) |

The written yield is high because the call is in the money: most of what it
collects is intrinsic value that goes back out. The call leg's net line is what
the calls actually kept.

**Year by year**

| Year | Put-protected | Butterfly-hedged | Buy & hold |
| --- | --- | --- | --- |
| 2011 | −2.89% | −14.46% | −24.39% |
| 2012 | +13.42% | +23.16% | +26.49% |
| 2013 | +3.15% | −1.61% | +7.23% |
| 2014 | +18.08% | +25.26% | +31.57% |
| 2015 | −0.03% | −0.87% | −4.26% |
| 2016 | +9.79% | +7.55% | +3.97% |
| 2017 | +16.33% | +20.69% | +29.89% |
| 2018 | +8.79% | +4.87% | +4.82% |
| 2019 | +7.90% | +17.06% | +13.61% |
| 2020 | +15.94% | +12.37% | +15.42% |
| 2021 | +10.91% | +17.93% | +25.97% |
| 2022 | −0.33% | +6.59% | +5.46% |
| *2023* | *not traded — gap between the windows* | | |
| 2024 | +10.26% | +12.52% | +10.04% |
| 2025 | +6.71% | +13.81% | +11.66% |
| 2026 to 11 Sep | +0.87% | −6.95% | −9.54% |

The pattern is the trade's nature. It lags in years NIFTY rallies hard (2012,
2014, 2017, 2021, 2025) and wins in falling or flat years (2011, 2015, 2016, 2018,
2026). Only once, in 2022, did it lose money in a year buy-and-hold made money.

---

## 2. The butterfly-hedged covered call

The best of the butterfly-hedged variants, chosen by the same rule restricted to butterflies.

**Position:** 94% NIFTYBEES, 6% cash. Calls on the **full** exposure. A 1:2:1 put
butterfly on the full exposure.

1. **Expiry:** the monthly closest to 30 days out (at least 7).
2. **Rebalance** to 94% / 6%.
3. **Sell calls** on the full exposure at the traded strike nearest 2% **above** NIFTY.
4. **Buy the butterfly:** sell 2× puts at the listed strike nearest 6% below NIFTY
   (the body), and buy 1× at a strike 3% of NIFTY above the body and 1× at 3% below.

**Adjust** — checked at each close while more than 2 days remain, in this order:

5. **Take profit:** if the call is worth 20% or less of its sale price, buy it back.
   No call until the next cycle.
6. **Roll up:** if NIFTY is 2% or more above the call's strike, buy it back and sell
   a new call 2% above the current level, same expiry.
7. **Roll down:** if NIFTY is 4% or more below its level at the last call sale, buy
   the call back and sell one 2% above the current level, same expiry, if that strike is lower.

**Exit and roll:** hold to expiry, settle at intrinsic, and open the next cycle the same day.

| | 2011–2022 | 2024–2026 |
| --- | --- | --- |
| CAGR / buy & hold | 9.28% / 10.15% | 6.72% / 4.00% |
| Max drawdown | −25.94% (Jan → Mar 2020) | −12.33% (Jan → Mar 2026, not yet recovered) |
| CAGR ÷ max drawdown | 0.36 | 0.55 |
| ROA | 9.89% | 6.11% |
| Written yield / call leg net / butterfly net | 20.36% / −1.82% / +0.55% | 11.11% / +4.36% / −1.56% |
| Costs a year | 0.52% | 0.28% |
| Cycles / made money / worst cycle | 145 / 61.4% / −15.88% | 33 / 66.7% / −10.81% |
| Adjustments | 176 | 29 |

It earns more than the recommended strategy in a bull market and about the same in
a flat one, but its worst fall is two and a half times deeper. In March 2020 NIFTY
fell through the whole butterfly within a single expiry, and the structure stopped
paying exactly when protection mattered. Its neighbours in the grid are also far
less stable (§4).

---

## 3. How the choice was made

**The grid.** 5,376 configurations, each run on both windows from a fresh start:

| Setting | Values tried |
| --- | --- |
| Call strike vs NIFTY | −1%, 0, +1%, +2%, +3%, +5%, +7% |
| Expiry target | ~30 or ~45 days |
| Exit | at expiry, or with 10 or 20 days left |
| Hedge | none; butterfly −4.5%/±2.5%, −3%/±2%, −6%/±3%, and −4.5%/±2.5% doubled; put −5%; put spread −3%/−8%; tail put −10% |
| Roll up at +2% / roll down at −4% | off or on, each |
| Take profit at 20% | off or on |
| Coverage | calls on all, or half, of the holding |

**The rule, fixed before the test window was read.** Among configurations whose
2011–2022 drawdown was no worse than buy-and-hold's and which made money in each of
2011–14, 2015–18 and 2019–22 (5,184 qualified), take the highest CAGR divided by max
drawdown. The butterfly variant is the same rule among butterflies only.

**Why not the highest CAGR.** It is 7% out of the money, calls on half the
holding, with a −6%/±3% butterfly: 10.46% in 2011–2022, the only kind of setting
that kept up with the bull market. On 2024–2026 it made 2.68% and ranked 5,245th of
5,376. Across the whole grid, the top tenth by calibration CAGR landed in the test
window's top quarter 3% of the time; the top tenth by CAGR ÷ drawdown did so 35% of
the time.

**What the grid says about each choice** (2011–2022 medians):

| Hedge | CAGR | Max drawdown | CAGR ÷ drawdown |
| --- | --- | --- | --- |
| Put −5% | 6.66% | −14.22% | **0.45** |
| Tail put −10% | 7.47% | −18.47% | 0.40 |
| Put spread −3%/−8% | 7.54% | −27.04% | 0.28 |
| None | 8.40% | −31.00% | 0.27 |
| Butterfly −6%/±3% | 7.84% | −32.18% | 0.25 |
| Butterfly −3%/±2% | 7.47% | −30.87% | 0.24 |
| Butterfly −4.5%/±2.5% | 7.42% | −32.29% | 0.23 |
| Butterfly −4.5%/±2.5%, doubled | 6.35% | −33.06% | 0.19 |

Every butterfly shape is no better than going unhedged on drawdown, and all cost
return. The top tenth of the grid by CAGR ÷ drawdown is 68% put-hedged and 32%
tail-put-hedged, with no butterflies at all. Further out-of-the-money calls earn
more and fall further (−1%: 6.27% / −27.0%; +7%: 8.28% / −31.9%). Rolling up helps
return; rolling down helps drawdown.

**For reference — the playbook's own rules** (2% above NIFTY, ~45 days, exit with 20
days left, −4.5%/±2.5% butterfly, no adjustments): 6.77% / −31.33% in 2011–2022 and
7.93% / −10.00% in 2024–2026. That test result is the best of the four strategies
shown here, but it was not what the rule picked, and choosing it now would be
choosing on the test window. It is also the most cost-sensitive: doubling spreads
takes 2011–2022 down to 5.64%.

---

## 4. Robustness

**Neighbours.** Change one setting of the recommended strategy at a time:

| Change | 2011–2022 CAGR / drawdown | 2024–2026 CAGR / drawdown |
| --- | --- | --- |
| *Recommended* | 8.22% / −9.83% | 6.57% / −6.20% |
| Call at NIFTY (not 1% below) | 8.14% / −10.64% | 6.24% / −7.65% |
| Call 2% above NIFTY | 8.37% / −11.77% | 5.33% / −9.82% |
| Call 5% above NIFTY | 9.13% / −13.48% | 4.68% / −11.64% |
| ~30-day expiry | 6.51% / −14.77% | 7.57% / −5.91% |
| Exit with 10 days left | 5.91% / −12.63% | 5.88% / −7.93% |
| No roll-down | 7.31% / −10.85% | 6.46% / −7.25% |
| Add roll-up | 7.83% / −13.52% | 6.29% / −7.80% |
| Add 20% take-profit | 7.73% / −9.78% | 6.96% / −7.88% |
| Calls on the full holding | 6.13% / −12.01% | 8.48% / −3.54% |

No change breaks it. The strategy sits on a plateau — puts plus in- or near-the-money
calls held to expiry — not on a lone spike. The butterfly variant's neighbours range
from 6.20% to 10.26% in 2011–2022 and from 1.69% to 7.57% in the test.

**Costs and cash.**

| | 2011–2022 | 2024–2026 |
| --- | --- | --- |
| As tested | 8.22% / −9.83% | 6.57% / −6.20% |
| Every bid-ask spread doubled | 8.04% / −9.93% | 6.46% / −6.25% |
| 10% cash buffer instead of 6% | 7.87% / −9.42% | 6.28% / −5.94% |

**Walk-forward.** Choosing by CAGR ÷ drawdown on 2015–18 alone picks the same trade
with calls on the full holding, and it ranked 19th of 5,376 on the test window.
Persistence is real but uneven: the top tenth on 2011–14 stayed in the top quarter on
2015–18 71% of the time (25% by chance), but from 2015–18 to 2019–22 only 18%. The
Covid crash and V-shaped recovery of 2020 rewarded the less-hedged trades.

**The regime matters more than the settings.** Over the whole grid, 2011–2022 CAGR
rank and 2024–2026 CAGR rank correlate at **−0.47**. Settings that give up the least
upside in a bull market are the ones that collect the least in a flat one. The
recommended strategy does not escape this — it lagged in every strong year — but it
has the smallest gap between its good and bad regimes of anything tested.

---

## 5. What each number means

| Term | Definition |
| --- | --- |
| **CAGR** | Compound annual growth of total equity — NIFTYBEES + cash + open options at mark — from the first to the last session of the window. |
| **Max drawdown** | The largest fall from a peak to a later trough in daily equity. |
| **Volatility** | Standard deviation of daily returns, annualised. |
| **CAGR ÷ max drawdown** | Return earned per unit of worst-case pain. The selection metric. |
| **ROA** | Return on assets: average annual profit divided by the average capital employed (ETF + cash). |
| **Written yield** | Premium received from every call sold in a year, re-sales after rolls included, net of spread and charges, divided by the average NIFTYBEES value. Includes intrinsic value on in-the-money strikes, so it overstates income. |
| **Call leg / hedge leg, net** | That leg's total cash result over the year — premium less buybacks and settlements, or put cost less payoffs — divided by average NIFTYBEES value. |
| **Costs** | Charges plus the half-spread paid on every fill, a year, divided by average equity. |
| **Cycle** | One expiry: from the day positions open to the day they settle. |

---

## 6. Assumptions and limits

- **End-of-day prices.** Every trade is at the session's close, plus a half-spread.
- **Only traded prices for trades.** Calls are sold only at strikes that traded
  that day. An untraded strike's close is NSE's settlement value and misses
  put-call parity by a median 1.8% of the future, so untraded legs are marked with
  Black-76 at the implied volatility of the same expiry's traded strikes.
- **Spreads and charges.** The half-spread is the larger of ₹0.25 or 0.5% of
  premium, and the larger of ₹0.50 or 3% on untraded strikes. Charges: STT on option
  sales (0.0625% of premium, 0.1% from Oct 2024), exchange and SEBI fees with GST,
  stamp duty on buys, ₹20 brokerage an order scaled to a ₹50 lakh book, 0.125% STT on
  exercised long options, and 0.05% impact plus stamp duty on ETF trades.
- **Proportional sizing.** Positions are fractional; §1.5 converts them to lots.
  Rounding will move a small book's results.
- **No interest on the cash buffer, and no taxes.** Option income is taxed as
  business income and ETF gains as capital gains; neither is modelled.
- **Margin is assumed, not simulated.** Pledged NIFTYBEES plus 6% cash is assumed
  to cover the short calls. No margin call or forced exit is modelled.
- **NIFTYBEES.** Divided by 10 before its 2019-12-19 unit split. Its price includes
  dividends — its ratio to the NIFTY price index rose from 10.06 to 11.30 over 15
  years. Before 2019 it traded ₹2–5 crore a day, fine for a ₹50 lakh book but not for
  a large one.
- **Two sessions with traded contracts only.** NSE published no bhavcopy for
  2013-10-09 or 2021-03-30; those days come from the Market Activity report.
- **Windows.** The test window is 2.7 years of one mostly flat regime ending in a
  fall. It confirms the calibration's risk profile. It cannot prove the edge will persist.

---

## 7. Reproducing

```bash
uv run python -m scripts.covered_call extract --out DIR     # R2 -> local Parquet, ~1 min
FNO_MIRROR=DIR uv run python -m scripts.covered_call smoke  # accounting must reconcile first
FNO_MIRROR=DIR uv run python -m scripts.covered_call sweep  # 5,376 configs x 2 windows, ~1 min on 8 cores
FNO_MIRROR=DIR uv run python -m scripts.covered_call analyse  # picks, walk-forward, stress, neighbours
```

`smoke` checks that final equity equals starting capital plus ETF P&L, plus option
cash flows, minus ETF costs, and that fills match the bhavcopy's closes. `analyse`
writes `analysis.json`, which holds every figure in this document.
