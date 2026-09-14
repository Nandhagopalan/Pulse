# NIFTY Options Strategy Playbook

**Status:** design notes for backtesting. Nothing here is built.
**Purpose:** a catalogue of option strategies written as rules precise enough to simulate.
It is the input to the options data work and the strategy experiments that follow.

Each strategy is set out the same way:

- **Idea:** what the position is for.
- **When to use it, and when not to.**
- **Construction:** strikes, expiry, sizing.
- **Worked example:** historical prices.
- **Adjustments:** trigger → action tables with IDs, so each rule can be switched on or
  off in a simulation.
- **Exit.**

Where a rule is not precise enough to code, the gap is named rather than filled with a
guess. Those gaps are collected in §18.

Worked examples use the lot sizes and index levels of their own dates (NIFTY lot 50,
BANKNIFTY lot 25 in 2022–23), so rupee figures don't carry over to today. §14 explains how
to restate rules in points, percentages and sessions-to-expiry before simulating.

---

## Contents

| § | Strategy | Family | Market view | Risk | Typical expiry | EOD-simulable (§16) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Covered call on an index ETF | Income, long index | Mildly bullish / neutral | Long-index downside | Monthly, ~45 DTE | **Yes** — core rules |
| 2 | Accumulate, then write calls | Long-term investing + income | Long-term bullish | Long-index downside | Monthly | **Yes** |
| 3 | Premium-based strangle | Short premium | Neutral | Unlimited | Weekly or monthly | Approximate |
| 4 | Directional ratio spread | Directional short premium | Bullish or bearish | Unlimited | Weekly | Approximate |
| 5 | Debit spread and ladder | Directional, risk-defined | Bullish or bearish | Defined | Weekly or monthly | Approximate |
| 6 | Credit spread | Directional, risk-defined | Bullish or bearish | Defined | Near expiry | Approximate |
| 7 | 1:2:1 butterfly | Directional or range, risk-defined | Directional or sideways | Defined | Weekly | Approximate |
| 8 | 2:3:1 butterfly | Directional, risk-defined | Directional | Defined | Weekly | Approximate |
| 9 | Iron fly | Neutral, risk-defined | Neutral or skewed | Defined | Weekly | Needs intraday |
| 10 | Batman (double ratio spread) | Neutral short premium | Neutral | Unlimited beyond the wings | Weekly / expiry day | Entry only |
| 11 | CPR + VWAP intraday spread | Intraday directional | Intraday | Defined | Weekly | Needs intraday |

§12 (capital allocation) · §13 (evaluation pitfalls) · §14 (market structure) · §15 (data) ·
§16 (simulation readiness) · §17 (reference trades) · §18 (open decisions).

### Vocabulary

| Term | Meaning |
| --- | --- |
| Credit / debit spread as building blocks | Most multi-leg positions decompose into spreads. An iron fly is two credit spreads; a butterfly is one debit spread plus one credit spread; a ratio spread is a debit spread with extra short legs. Many adjustments close one component spread and keep the other. |
| Index ETF | An exchange-traded fund tracking the index, e.g. NIFTYBEES (NIFTY 50) or BANKBEES (NIFTY Bank). |
| Lot-equivalent | The ETF quantity whose value equals one option lot's notional: lot size × index spot. |
| Pledge | Using held securities as collateral margin for short options. |
| Loss free | Rolling a leg so that the worst outcome at expiry is ≥ 0. |
| Match premium | In a strangle, rolling the profitable side toward spot until its premium equals the losing side's. |
| Double stop | Stop-loss that triggers when a sold option's price reaches 2× the sale price. |
| Extra sells | Selling additional far-OTM options to collect the credit that pays for an adjustment. |
| Reverse buying | Rolling a long hedge *toward* spot to reduce the worst-case loss on that side. |
| Moneyness offsets | "300 points OTM" means a strike 300 index points away from spot, on the out-of-the-money side. |

---

## 1. Covered call on an index ETF

### 1.1 Idea

Hold one lot-equivalent of index exposure as an ETF and sell one monthly index call against
it every cycle. A put butterfly limits losses in a moderate fall, and the ETF is pledged as
collateral for the option margin.

The payoff:
- **Upside:** capped.
- **Downside:** close to the full index loss below the butterfly.
- **Best case:** a flat or gently rising month.

It suits capital that is already committed to the index long-term and wants a monthly
premium on top.

### 1.2 Parameters

| Parameter | Rule | Default |
| --- | --- | --- |
| Long leg | Index ETF, or the index itself as a proxy (§18) | NIFTYBEES |
| ETF quantity | lot size × index spot ÷ ETF price, rounded to a practical quantity | 1 lot-equivalent |
| Entry | Monthly expiry, about 45 calendar days before expiry | 45 DTE |
| Short calls | One lot per lot-equivalent of ETF | 1 |
| Call strike, bullish view | 300 points OTM | — |
| Call strike, bearish view | 100 points ITM | — |
| Call strike, no view | The strike whose premium meets the cycle's return target | — |
| Hedge | 1:2:1 put butterfly on the same expiry | 1 per call lot |
| Hedge placement | Body (short ×2) about 4.5% below spot; equal wings about 2.5% either side of the body | See §1.4 |
| Hedge financing | Optional far-OTM option sales against the pledged ETF, sized to the butterfly's cost | Off |
| Exit | After 20–25 days, or hold to expiry | 20–25 days |
| Roll | Re-enter the next month at the same rules | Monthly |

```
ETF quantity = option lot size × index spot ÷ ETF price
```

### 1.3 Worked example — NIFTY, 17 May 2023 (June expiry 29 Jun, ~43 DTE, lot 50)

Sizing: 50 × 18,270 = ₹9,13,500 ÷ ₹200.10 = 4,565 units (4,500 as a round quantity).

| Leg | Qty | Price |
| --- | --- | --- |
| Buy NIFTYBEES | 4,565 units | ₹200.10 |
| Sell 18200 CE (29 Jun) | 1 lot | ₹405 |
| Buy 18000 PE (29 Jun) | 1 lot | ₹189 |
| Sell 17500 PE (29 Jun) | 2 lots | ₹90 |
| Buy 17000 PE (29 Jun) | 1 lot | ₹35 |

- **Butterfly:** costs 189 − 2×90 + 35 = **44 points = ₹2,200**. Maximum value ₹22,800 at
  17,500; breakevens 17,044 and 17,956.
- **Why an ITM call here:** with spot at 18,270 the 18200 CE is 70 points ITM, so ₹70 of
  its ₹405 premium is intrinsic and **₹335 is time value**. If NIFTY finishes flat, the
  position keeps the ₹335. On a low-premium index, an ITM strike is how the cycle
  collects meaningful time value.
- **Never hedge with a bare 1:2 put ratio.** The 18000/17500 legs without the 17000 PE have
  unlimited loss below ~17,009. The lower wing is what makes the hedge bounded.

Today's equivalent notional is NIFTY ≈ 23,400 × lot 65 ≈ ₹15.2L per lot-equivalent.

### 1.4 Variant — a higher-premium index (BANKNIFTY)

On an index that pays usable premium well out of the money, sell the call **OTM** instead.

Example (BANKNIFTY 41,350, 27 Apr 2023 expiry, lot 25, ~50 DTE):

| Leg | Qty | Price |
| --- | --- | --- |
| Buy BANKBEES | 2,475 units | ₹417.84 (41,350 × 25 ÷ 417.84) |
| Sell 42000 CE | 25 | ₹699.40 |
| Buy 40500 PE | 50 | ₹420.80 |
| Sell 39500 PE | 100 | ₹230.55 |
| Buy 38500 PE | 50 | ₹123.00 |

This uses **two** butterflies per call lot. The butterflies cost ₹4,135, with a maximum
value of ₹45,865 at 39,500. The hedge ratio is a parameter to test (1 or 2 per lot).

Butterfly placement in three examples:

| Example | Spot | Upper wing | Body (short ×2) | Lower wing |
| --- | --- | --- | --- | --- |
| NIFTY, May 2023 | 18,270 | 18000 (−1.5%) | 17500 (−4.2%) | 17000 (−7.0%) |
| BANKNIFTY, Nov 2022 | 42,030 | 41000 (−2.5%) | 40000 (−4.8%) | 39000 (−7.2%) |
| BANKNIFTY, Mar 2023 | 41,350 | 40500 (−2.1%) | 39500 (−4.5%) | 38500 (−6.9%) |

### 1.5 Adjustments

| ID | Trigger | Action |
| --- | --- | --- |
| A1 | The index keeps falling across the cycle | Sell weekly calls against the pledged ETF for extra premium. |
| A2 | The index falls through the butterfly (~1,000–1,500 points) | Add a second put butterfly, or buy a cheap weekly put (₹10–20) as tail cover. |
| A3 | The index rallies ~1,000 points | **In a bull regime:** exit both the ETF and the call. **In a bear regime:** exit only the call and keep the ETF. Needs a regime definition (§18). |
| A4 | Alternative hedge | A longer-dated put, or a plain long put, instead of the butterfly. Costs more premium but has no lower wing. |

### 1.6 Exit and roll

- **Timing:** exit after 20–25 days of a ~45-DTE entry, or hold to expiry.
- **After a down cycle:** reinvest the call premium and any butterfly profit into more ETF
  units at the lower price.
- **Next cycle:** re-enter with the same rules.

### 1.7 Payoff at expiry — NIFTY example

One lot-equivalent, ETF mark-to-market included, butterfly included:

| NIFTY at expiry | Move | Net | Return on ₹9.14L |
| --- | --- | --- | --- |
| 19,000 | +4.0% | +₹14,550 | +1.59% (upside capped) |
| 18,270 | flat | +₹14,550 | +1.59% |
| 18,000 | −1.5% | +₹4,550 | +0.50% |
| 17,500 | −4.2% | +₹4,550 | +0.50% |
| 17,454 | −4.5% | ₹0 | breakeven |
| 17,000 | −7.0% | −₹45,450 | −4.98% |
| 16,000 | −12.4% | −₹95,450 | −10.45% |

- **Maximum:** +₹14,550 at or above the 18,200 strike. A rising market earns no more than
  a flat one.
- **Butterfly band:** between 18,000 and 17,500 the butterfly offsets the ETF one-for-one,
  so the result is flat at +0.50%.
- **Steep zone:** between 17,500 and 17,000 the two short puts make losses grow at **twice**
  the index's rate.
- **Below 17,000:** the butterfly is worthless, leaving a plain long index minus the call
  premium.

Across many cycles the risk is path-dependent. A crash is absorbed almost in full; rolling
the call down afterwards then caps part of the recovery. A single-cycle table can't show
this — only a multi-year simulation can.

---

## 2. Accumulate, then write calls

A long-horizon plan: build an ETF holding gradually, then run §1 against it.

| Rule | Detail |
| --- | --- |
| Target | A fixed number of ETF units a year (example: 1,000 BANKBEES units ≈ 83 a month) |
| Horizon | 5–8 years, until the holding covers the lot-equivalents intended for call writing |
| Timing of buys | Don't buy the year's quantity at once. Buy on dips to improve the average price. |
| While waiting | Sell puts to earn premium on the cash set aside. On a large down day, invest the accumulated premium. |
| After accumulation | Every month, sell calls against the holding (§1). |

Example accumulation: month 1, 65 units at ₹424; month 2, 40 units at ₹406.50; average
₹415.25.

**Gaps to define before simulating:**
- **"Dip":** what counts as one — e.g. N% below a moving average, or below the last buy
  price.
- **Waiting-period puts:** strike, size and expiry.
- **Missed target:** what happens if dips don't come and the yearly quantity isn't reached.

---

## 3. Premium-based strangle

### 3.1 Strike selection — three methods

1. **Price action:** sell the call at a swing high and the put at a swing low.
2. **Previous week's range:** sell the call at last week's high and the put at last week's
   low.
3. **By premium:** sell the strikes trading at a fixed premium. This is the default here.

| Variant | Premium per leg (at 2023 index levels) | Entry timing |
| --- | --- | --- |
| NIFTY weekly | ₹7–10 | 5–6 sessions before the next weekly expiry |
| BANKNIFTY weekly | ~₹20 | As above |
| NIFTY monthly | ₹20–25 | About 45 days before expiry. Adjusted by price levels rather than premium doubling, because monthly premiums double quickly. |

Rupee premium bands depend on the index level and volatility, so §14 covers how to
normalise them.

### 3.2 When not to deploy

- **Scheduled events:** policy decisions, the budget, election results.
- **Strong trends:** sell directionally instead (§4–§6).

### 3.3 Adjustments — weekly

| ID | Trigger | Action |
| --- | --- | --- |
| S1 | Within 1–2 sessions of entry, one side's premium **doubles** (e.g. ₹8 → ₹16) | Exit that side at a loss. Sell **2 lots** of the same side at a strike now trading near the original premium (~₹8). Position: 1 lot short put at ₹8, 2 lots short call at ₹8. |
| S2 | After S1, the new short side doubles again within a session | Exit that side at a loss and **don't re-enter that side** this cycle. Two stops on one side indicates a trend. |
| S3 | After ~3 sessions, one side's premium doubles | Book the profitable side and re-sell it closer to spot, matching the losing side's premium. Keep matching as the index moves, up to a straddle, then hold to expiry. Reaching a straddle is the worst case. |

The same rules apply to the put side.

### 3.4 Exit and variants

- **Profit exit:** close when the premium has decayed **≥ 85%**, then enter the next
  cycle.
- **Skewed strangle with an existing hedge:** if a put butterfly is already on, sell
  2 lots put + 1 lot call. A fall reaches the butterfly first.
- **Price-action filter:** sell at the same premium, but skip the side that price action
  says is trending.

**Gap:** the "level-based adjustments" for the monthly variant need a definition (§18).

---

## 4. Directional ratio spread (weekly)

For weeks with a clear directional view.

### 4.1 Worked example (bullish; NIFTY 17,594.35; weekly expiry 09 Mar 2023; lot 50)

| Leg | Lots | Price |
| --- | --- | --- |
| Buy 17700 CE | 1 | ₹33.20 |
| Sell 17750 CE | 2 | ₹20.60 |
| Sell 17300 PE | 1 | ₹12.00 |

- **Net credit:** ₹1,000.
- **Max profit:** ₹3,500 at 17,750.
- **Breakevens:** 17,280 and 17,820.
- **Risk:** **unlimited on both sides**.
- **Margin:** ~₹1.24L at that date.

The structure is a 1:2 call ratio spread above spot plus a short OTM put below. For a
bearish view, mirror it.

### 4.2 Adjustments

| ID | Trigger | Action |
| --- | --- | --- |
| R1 | Index moves in the expected direction | Do nothing until spot reaches the short call strike (17750). Then buy a 17850 or 17900 CE to turn the call side into a (broken-wing) butterfly. |
| R2 | Index moves against the view | Either close the short put at 2× its sale price, or buy a lower put (17200 or 17100 PE) to cap the loss. Buying either put against the short 17300 PE forms a bull put **credit** spread. |
| R3 | Index doesn't move | Close the short legs and re-sell nearer spot. **Not recommended:** close to spot, a sharp move leaves no room to adjust. |

**Gap:** the weekly directional signal must be defined mechanically (§18).

---

## 5. Debit spread and ladder

### 5.1 Construction

- **Structure:** buy the higher-premium strike, sell the lower-premium strike.
- **Timing:** any session before expiry works, because the risk-reward is favourable.
- **Strike choice:** OTM spreads have a better risk-reward than ATM ones.
- **Capital:** under ₹50k per spread at 2023 lot sizes.

Examples (bearish; BANKNIFTY 39,395.35; 29 Mar 2023 expiry; lot 25):

| Variant | Buy | Sell | Debit | Max profit |
| --- | --- | --- | --- | --- |
| Near the money | 39200 PE ₹250 | 38700 PE ₹127 | ₹3,150 | ₹9,350 |
| Further OTM | 39000 PE ₹198 | 38500 PE ₹91.10 | ₹2,673 | ₹9,828 |

### 5.2 Adjustments

Plan both directions before entering.

**When it goes your way** (spot below the long 39000 strike):

| ID | Action | Result |
| --- | --- | --- |
| D1 | Sell the 39000 PE and buy a 38900 or 38800 PE | Loss free |
| D2 | Sell one more 38500 PE and buy a 38000 PE | 1:2:1 butterfly |

**When it goes against you** — the adjustment depends on days to expiry:

| ID | DTE | Action |
| --- | --- | --- |
| D3 | 2 | Sell one more 38500 PE → 1:2 ratio |
| D4 | 1 | Sell two more 38500 PE → 1:3 ratio |
| D5 | After D3/D4, index reverses back to the long strike | Convert to a butterfly, or book profit |
| D6 | 2 (alternative to D3) | Exit the long 39000 PE at a loss and sell a 40000 CE → strangle, managed by §3 |

### 5.3 Systematic weekly variant

Every week, regardless of view, open a **200-point debit spread**. Example: BANKNIFTY
40,307; buy 40000 PE at ₹199, sell 39800 PE at ₹168.35; debit ₹766.

Wait 3 sessions. If the index has gone against the spread, sell an OTM put whose
**premium × quantity ≈ the current loss** to recover it. At lot 25, recovering ~₹700
needs a ~₹28 put, or two lots of a ~₹15 put.

### 5.4 Monthly debit spread → ladder (NIFTY, 27 Apr 2023 expiry, lot 50)

| Step | Date | NIFTY | Legs after the step | Result |
| --- | --- | --- | --- | --- |
| Entry | 2023-03-10 | — | Buy 17000 PE · Sell 16600 PE ₹71.50 | Max loss ~₹3,500; small bearish view |
| Adjust 1 | 2023-03-13 | 17,154 | Sell 17000 PE (+₹4,858 booked) · Buy 16900 PE ₹207 | Max loss cut to ₹1,918; breakeven 16,862 |
| Adjust 2 | 2023-03-14 | 17,022 | + Sell 16500 PE ₹148 · Buy 16200 PE ₹96.50 | Worst case **+₹658**, best **+₹15,658** |

Adjust 1 rolls the long leg down, banking its gain. Adjust 2 adds a lower credit spread.
The final position is +16900 / −16600 / −16500 / +16200 PE, a broken put condor
("ladder"), and it is loss free.

---

## 6. Credit spread

- **Structure:** sell the higher-premium strike, buy the lower-premium strike.
- **Odds:** poor risk-reward but a high probability of profit. Only use it with high
  confidence in direction.
- **Timing:** 1–2 sessions before a weekly expiry, or about a week before a monthly.
- **Direction:** bearish → call credit spread; bullish → put credit spread.
- **Capital:** under ₹50k at 2023 lot sizes.

Example (bearish; BANKNIFTY 39,395; lot 25): sell 40000 CE at ₹104.05, buy 40300 CE at
₹48.80. Credit ₹1,381, max loss ₹6,119.

| ID | Trigger | Action |
| --- | --- | --- |
| C1 | Goes your way | Book the profit. **Don't roll toward spot.** |
| C2 | Goes against you | Sell a 40000 PE and buy a 39700 PE → iron fly, managed by §9 |

---

## 7. 1:2:1 butterfly

**Use it for:** a directional view, or a sideways market managed with adjustments. VIX
level doesn't matter.

**Construction:** buy 1 at the upper strike, sell 2 at the body, buy 1 at the lower
strike, with equal wings. Wing width is 300–600 points on BANKNIFTY, depending on the
range and risk wanted.

### 7.1 Worked example — put butterfly (bearish; BANKNIFTY ~39,000; 01 Sep 2022 weekly)

| Leg | Qty | Price |
| --- | --- | --- |
| Buy 39000 PE | 50 | ₹248.90 |
| Sell 38500 PE | 100 | ₹118.55 |
| Buy 38000 PE | 50 | ₹58.20 |

- **Cost:** 70 points debit = **₹3,500**, the max loss.
- **Max profit:** ₹21,500 at 38,500.
- **Breakevens:** 38,070 and 38,930.
- **Capital:** about ₹50k per lot at that date.

### 7.2 Adjustments — strike-based

| ID | Trigger | Action |
| --- | --- | --- |
| F1 | In your favour and profit reaches 5–8% of capital | Book it and look for the next trade. |
| F2 | Spot reaches the body strike (38500) | Move both wings 100 points in: 39000 PE → 38900 PE, 38000 PE → 38100 PE. The worst case is then near zero; hold to expiry. |
| F3 | Against you: last week's high, or the latest swing high, breaks | Exit at the smaller loss. |
| F4 | Against you (alternative) | Sell an OTM put whose premium × quantity ≈ the current loss. |
| F5 | Against you (alternative) | Move the two short 38500 PE up to 38600 PE, so the upside loss is ~0. |

### 7.3 Adjustments — treating it as a credit spread + debit spread

Example (bullish call butterfly; BANKNIFTY 43,050; 08 Dec 2022 weekly): buy 43200 CE ×1,
sell 43700 CE ×2, buy 44200 CE ×1. Read it as a **debit spread** (43200/43700) plus a
**credit spread** (43700/44200).

| ID | Trigger | Action |
| --- | --- | --- |
| F6 | Against you: last week's low, or the latest swing low, breaks and holds | Close the credit spread (in profit). Keep the debit spread and sell calls at or above 44500 → 1:1:2 ratio. |
| F7 | Spot runs through the upper wing (44200) and key resistance with no chance to adjust | Close the debit spread (in profit). The 43700/44200 credit spread is now losing: sell puts below support. If spot retraces to 43700, close everything, including the extra puts. |

For a bullish view use calls; for a bearish view use puts, with the same rules.

---

## 8. 2:3:1 butterfly

**Structure:** buy 2 near the money, sell 3 at the body, and buy 1 far wing at **twice the
near spacing** — e.g. 300 points to the body, 600 points to the far wing.

At that geometry the payoff beyond the far wing is flat at zero, so the extra short is
covered. Max loss is the debit on both sides. Profit potential is higher than a 1:2:1
butterfly for a similar debit.

**Use it for:** a directional view; any VIX.

**Bullish example** (BANKNIFTY 39,450; 20 Oct 2022 weekly; lot 25):

| Leg | Qty | Price |
| --- | --- | --- |
| Buy 39500 CE | 50 | ₹423.60 |
| Sell 39800 CE | 75 | ₹285.43 |
| Buy 40400 CE | 25 | ₹107.00 |

Debit ₹2,448 (max loss); max profit ₹12,553 at 39,800; breakevens 39,549 and 40,302.

**Bearish example** (BANKNIFTY 39,909; 02 Mar 2023 weekly): buy 2× 39800 PE at ₹245, sell
3× 39500 PE at ₹145, buy 1× 38900 PE at ₹47.25. Debit ₹2,556; max profit ₹12,444.

| ID | Trigger (bullish version) | Action |
| --- | --- | --- |
| B1 | Index moves toward the view | Do nothing until spot is near the body (40,200 in the example). |
| B2 | Spot breaks **39,000** (against the view) | Shift **2 of the 3** short 39800 CE down to 39700 CE, collecting more credit to reduce the downside loss. |
| B3 | Spot breaks **40,200** (through the body) | Move the long 40400 CE to 40300 CE → broken-wing butterfly, reducing the upside loss. |

For a bearish view, the same rules apply with puts.

---

## 9. Iron fly

**Structure:** a short ATM straddle plus long wings. Use it with a neutral view, or skew the
fly toward a directional view to widen the breakeven that way.

**Example** (BANKNIFTY 43,600; 15 Dec 2022 weekly): sell 43600 CE + 43600 PE; buy 43000 PE,
buy 44200 CE (600-point wings; 300–600 is typical). Breakevens are about 43,200 and 44,000.

**Wing choice** trades off three things:
- **Range and max profit:** wide wings give more of both.
- **Max loss:** near wings give less, and need less monitoring.

**Adjustment menu:** broken-wing fly, extra sells, reverse buying, butterfly or ladder,
ratio spread, debit spread. The procedure below uses **extra sells + reverse buying**.

**Setup:** mark support and resistance on the 1-hour chart (example: R = 43,800,
S = 43,400). Don't adjust until a level is tested **and a 15-minute candle holds** beyond
it.

| ID | Trigger | Action |
| --- | --- | --- |
| I1 | Resistance (43,800) tested and held for 15 min | Roll the long put up: 43000 PE → 43200 PE, paying ₹20–30. Immediately sell puts below 42800 for the same amount. |
| I2 | 44,000 breaks and holds | Roll the long put 43200 → 43300 PE (downside loss free). When the extra-sell put has decayed 80%, close it and sell next week's puts below 42500. |
| I3 | Spot retraces to the straddle, or back below resistance | Roll the long call 44200 → 44000 CE (reverse buying): both sides loss free. Sell next week's calls above 45000 for extra credit. |
| I4 | Support (43,400) tested and held for 15 min | Mirror of I1: roll the long call 44200 → 44000 CE for ₹20–30; sell calls above 44500. |
| I5 | 43,000 breaks and holds | Mirror of I2: roll the long call 44000 → 43800 CE; after 80% decay, roll the extra sells to next week. |
| I6 | Spot retraces up after I4/I5 | Mirror of I3: roll the long put 43000 → 43200 PE; sell next week's puts below 42500. |

---

## 10. Batman (double ratio spread)

**Structure:** a call ratio spread above spot plus a put ratio spread below it. Neutral
view. Better suited to a lower-volatility index such as NIFTY.

**Example** (NIFTY ~18,195; 05 Jan 2023 weekly; lot 50):

| Leg | Lots | Price |
| --- | --- | --- |
| Buy 18100 PE | 1 | ₹72.55 |
| Sell 17950 PE | 2 | ₹36.15 |
| Buy 18300 CE | 1 | ₹69.95 |
| Sell 18450 CE | 2 | ₹27.55 |

- **Cost:** net debit ₹755, which is the loss if spot stays between the long strikes.
- **Max profit:** ₹6,745 at either short strike.
- **Breakevens:** 17,815 · 18,085 · 18,315 · 18,585. Unlimited risk beyond the outer two.
- **Margin:** ~₹1.14L at that date.

**Construction rules:**
- **Premium match:** choose short strikes so that **2 × short premium ≈ long premium** on
  each side, keeping the middle-range loss small. The put side above matches
  (2 × 36.15 ≈ 72.55); the call side doesn't (2 × 27.55 = 55.10 vs 69.95).
- **Spacing:** 100/150/200 points between long and short strikes.
- **Ratio:** start at 1:2. 1:3 or 1:4 raises both the credit and the tail risk.
- **Avoid:** event days and results days.
- **Expiry-day version:** enter in the session before expiry and close on expiry day.

| ID | Adjustment | Trigger | Action |
| --- | --- | --- | --- |
| T1 | Convert to strangle | Spot still between the long strikes after **2–3 sessions**. Not sooner, or it turns into repeated adjusting. | Close both long legs at a loss and manage the four shorts as a strangle: if one side doubles, close the other in profit and match premiums (§3). |
| T2 | Convert to butterfly | Spot reaches one side's **short strike** (no action before that) | Buy 1 lot one spacing beyond: e.g. 18600 CE → call-side 1:2:1 butterfly. For a 1:4 ratio, buy 3. |
| T3 | Increase the ratio | After T2 on one side | Raise the other side's shorts from 1:2 to 1:4. Spot is now far from that side, so the extra credit is low-risk. |

---

## 11. CPR + VWAP intraday spread

- **Chart:** 5-minute.
- **Indicators:** Central Pivot Range (CPR) with R1/S1, VWAP, previous day high/low.
- **Premise:** price is drawn back toward the CPR after an open near R1 or S1.
- **Instrument:** a debit spread, never a naked option.

| Setup | Condition | Entry | Example |
| --- | --- | --- | --- |
| Bearish | Open near **R1 and the previous day high** | When a 5-min candle trades below VWAP, enter as the next candle breaks its low. Buy the ATM PE, sell a PE at the CPR-level strike. | 17 Mar 2023: buy 17050 PE, sell 16900 PE |
| Bullish | Open near **S1 and the previous day low** | When a 5-min candle trades above VWAP, enter as the next candle breaks its high. Buy the ATM CE, sell a CE at the CPR-level strike. | 16 Mar 2023: buy 16900 CE, sell 17050 CE |

**Pattern reading at the CPR:** in the bearish setup, a W at the CPR means price returns to
the day high; an M means the CPR breaks and price trends lower. The bullish setup is the
mirror image.

| ID | Trigger | Action |
| --- | --- | --- |
| V1 | Profit reaches ~₹1,200–1,500 per lot | Exit, or make it loss free: when price reaches ~16,950/16,900, sell the 17050 PE and buy a 17000 PE. |
| V2 | Price crosses and holds the day high (bearish setup) | Sell one more CPR-strike PE → 1:2 ratio, then exit at zero or minimum loss. |

---

## 12. Capital allocation templates

Two sample splits to test, not recommendations:

| Template | Allocation |
| --- | --- |
| Premium-heavy | 70% short strangles (§3); 30% risk-defined spreads and butterflies (§5–§8) |
| Laddered by expiry | 30% next month's strangles · 30% this month's strangles · 30% weekly spreads and ratios · 10% reserved for adjustments |

---

## 13. Evaluation pitfalls

These are easy to get wrong when judging these strategies by hand. The simulation must
handle each one explicitly.

| # | Pitfall | Correct treatment |
| --- | --- | --- |
| 1 | Leaving out the long leg's loss in a covered call because "the ETF has no expiry" | Mark every leg to market every cycle. In the §1.7 example, including the ETF turns an apparent +2% (at 18,000) and +4% (at 17,500) into +0.50% each. |
| 2 | Mixing monthly and annual return targets | 2% a month compounds to 26.8% a year; 12–14% a year is ~1% a month. Report one convention. |
| 3 | Swapping intrinsic and time value | Intrinsic = max(0, spot − strike) for a call. Only the time value decays to the seller. |
| 4 | Rules of thumb for ETF moves (e.g. "100 index points = ₹1 on the ETF") | Use the actual index ÷ ETF price ratio at entry. It drifts with dividends and expense ratio, and can be off by ~10%. |
| 5 | Offsetting a loss by "selling a put worth loss ÷ lot size" | Use premium × **position quantity** ≈ loss. A two-lot position needs twice the premium. |
| 6 | Mislabelling spreads | Buying a lower put against a short put is always a bull put *credit* spread. Label legs from the net cash flow. |
| 7 | Judging a strategy on a few months | A covered call beats nothing in a rally — it lags the plain ETF. Evaluate across full cycles that include sharp falls and recoveries, against buy-and-hold. |
| 8 | Unadjusted ETF history | NIFTYBEES has an unadjusted 1:10 unit split on 2019-12-19 in the lake (k = 9.93). Adjust it, or use the index as the long leg. |

---

## 14. Market structure and unit conventions

Checked against the NSE F&O bhavcopy for 2026-09-11. Lot sizes and expiry calendars have
changed several times over the history, so a simulation must read both from each session's
bhavcopy, never from constants.

| Then (2022–23) | Now (Sep 2026) | How to restate rules |
| --- | --- | --- |
| NIFTY weekly expiry on Thursday | NIFTY expiries on **Tuesday** (monthly = last Tuesday); holiday shifts exist (23 Nov 2026 is a Monday) | Express entry timing as **sessions to expiry**, not weekdays. |
| BANKNIFTY had weekly contracts | BANKNIFTY, FINNIFTY and MIDCPNIFTY list **monthly contracts only** | BANKNIFTY weekly examples can be backtested on history but not traded live. Port them to NIFTY weeklies or BANKNIFTY monthlies. |
| NIFTY lot 50, BANKNIFTY lot 25 | NIFTY **65**, BANKNIFTY **30** | State thresholds in points, % of premium, or % of capital, not rupees. |
| NIFTY ~17,000–18,500 | NIFTY ~23,400 | Point offsets ("300 points OTM") and rupee premium bands ("₹7–10") mean different moneyness at different levels. Normalise by % of spot, volatility, or delta (§18). |

---

## 15. Data required

| Id | Dataset | Needed for | In the lake today? |
| --- | --- | --- | --- |
| D1 | **NIFTY option chain, EOD** — every strike and expiry: close, settle, OI, contracts, lot size, underlying | Every NIFTY strategy | **No.** Source: NSE F&O bhavcopy, legacy 2001–2023 and UDiFF 2024→. Traps: volume is in contracts but OI is in units; legacy `SETTLE_PR` on option rows carries the underlying, not the option's settlement price. |
| D2 | BANKNIFTY option chain, EOD | BANKNIFTY variants; reference trades §17 | **No.** Same files as D1. |
| D3 | NIFTY 50 daily OHLC | Spot, moneyness, levels, weekly high/low | Yes, `curated/index_daily` from 2013. Earlier, from D1's underlying column. |
| D4 | NIFTY Bank daily OHLC | Same, for BANKNIFTY variants | Yes, from 2013 |
| D5 | NIFTYBEES / BANKBEES daily close | ETF leg of §1–§2 | Yes, `curated/daily` from 2007. Split caveat in §13. |
| D6 | India VIX daily | Regime context; strike normalisation | Yes, `curated/index_daily` from 2013 |
| D7 | Weekly high/low, daily swing points | "Last week's high/low", "swing high/low" | Derivable from D3/D4. Hourly swings need D11. |
| D8 | Event calendar — monetary policy, budget, election results, results season | "Don't deploy on events" (§3, §10) | **No.** Small, hand-curated. |
| D9 | Transaction costs by date — brokerage, STT, exchange fees, SEBI fee, stamp duty, GST | Every strategy; short-premium returns are cost-sensitive | **No.** Small config table; STT on options has changed over time. |
| D10 | Margin requirement by date (SPAN + exposure) | Return on capital for strangles, ratios, Batman | **No.** NSE clearing publishes SPAN parameter files; availability and depth not yet verified. |
| D11 | Intraday bars (1- or 5-minute), index and options | §11, the 15-min hold rules in §9, intraday stops | **No, and out of scope.** Pulse is end-of-day by design. |
| D12 | Risk-free rate (e.g. 91-day T-bill) | Only if strikes are chosen by delta or IV is computed | **No.** Optional. |

Which strategies need which data:

| Strategy | D1 | D2 | D3/D4 | D5 | D6 | D7 | D8 | D9 | D10 | D11 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| §1 Covered call | ● | ○ | ● | ● | ○ | ○ | | ● | | |
| §2 Accumulate, then write | ● | ○ | ● | ● | | | | ● | | |
| §3 Strangle | ● | ○ | ● | | ○ | ○ | ● | ● | ● | ○ |
| §4 Ratio spread | ● | | ● | | ○ | ○ | ○ | ● | ● | ○ |
| §5 Debit spread and ladder | ● | ○ | ● | | | ● | | ● | ○ | ○ |
| §6 Credit spread | ● | ○ | ● | | | | | ● | ● | ○ |
| §7 1:2:1 butterfly | ● | ○ | ● | | | ● | | ● | | ○ |
| §8 2:3:1 butterfly | ● | ○ | ● | | | ● | | ● | | ○ |
| §9 Iron fly | ● | ○ | ● | | | | | ● | ○ | ● |
| §10 Batman | ● | | ● | | ○ | | ● | ● | ● | ● |
| §11 CPR + VWAP | | | | | | | | ● | | ● |

● required · ○ useful, or needed for one variant

---

## 16. Simulation readiness

Three grades:

- **A — faithful at EOD.** Every rule can be evaluated on daily closes.
- **B — EOD approximation with a known bias.** For stops on short premium, the bias is
  usually **optimistic**: an intraday breach that forced a real loss-taking adjustment
  never appears in the closes.
- **C — needs intraday data.** Not simulable in Pulse.

| Strategy | Grade | Why |
| --- | --- | --- |
| §1 Covered call — entry at N DTE, strike by moneyness, butterfly, exit at M days or expiry, reinvest | **A** | Every rule is a date or a price at a close. |
| §1 adjustments A1–A3 | B | Need a mechanical regime definition. |
| §2 Accumulate, then write | A (buying) / B (put selling) | Put rules to be defined. |
| §3 Strangle — entry by premium, 85% decay exit | A | Decided on closes. |
| §3 "premium doubles" adjustments | B | Close-based doubling misses intraday spikes. |
| §4 Ratio spread | B | Needs a direction signal; the 2× stop is intraday in practice. Gap moves *are* captured close to close. |
| §5 Debit spread and ladder | B | Triggers are strike touches and DTE, approximable on closes. |
| §6 Credit spread | B | As §5. |
| §7 1:2:1 butterfly | B | "Reaches the body", "breaks last week's level" on closes. Hourly-swing variants are C. |
| §8 2:3:1 butterfly | B | Level breaks on closes. |
| §9 Iron fly | C | Adjustments require a 15-minute hold at 1-hour levels. |
| §10 Batman | B (entry to expiry) / C (adjustments) | Can be run as enter at close T−1, settle at T. |
| §11 CPR + VWAP | C | 5-minute bars. |

Suggested order:

1. **§1 covered call** — grade A, and the §17 reference trades check the engine first.
2. **§2** accumulate, then write.
3. **§3** strangle, monthly variant.
4. **§4–§8** directional spreads and butterflies.

---

## 17. Reference trades for calibration

Historical trades with known prices. Replay them through the data and P&L engine before
any long run; if the engine can't reproduce them to within slippage, a long run means
nothing. Entry and exit prices were taken intraday (~3:15 PM), so bhavcopy closes will
differ slightly.

| Case | Legs and prices | Expected result |
| --- | --- | --- |
| **K1** BANKNIFTY covered call, 2022-10-14 → 2022-11-14 (no hedge) | 2,470 BANKBEES at ₹397.86 → ₹425.79; short 39500 CE (Nov 2022) at ₹1,210 → ₹2,697 | ETF +₹68,987 · call −₹37,175 · **net +₹31,812** (index +7.11%) |
| **K2** BANKNIFTY covered call with butterflies, 2022-11-14 → 2022-12-09 | 2,472 BANKBEES at ₹425.15 → ₹441.34; short 42500 CE (29 Dec 2022) at ₹1,000 → ₹1,430; put butterflies 41000/40000/39000 × 50/100/50 at ₹532/308/175 (₹4,550 debit) | ETF +₹40,021.68 · call −₹10,750 · butterflies −₹3,715 · **net +₹25,556.68** (index +3.86%) |
| **K3** NIFTY covered call entry, 2023-05-17 | NIFTYBEES ₹200.10; 18200 CE (29 Jun 2023) at ₹405; 18000/17500/17000 PE at ₹189/90/35 | Entry prices; checks strike lookup, DTE, and the §1.7 payoff |
| **K4** Ladder, 2023-03-10 → 2023-03-14 | 16900/16600/16500/16200 PE (27 Apr 2023) at ₹207/71.50/148/96.50 | Worst case +₹658 · best +₹15,658 |

K1 and K2 are both rising months. In both, the covered call made money but less than
holding the ETF alone. They validate the arithmetic, not the strategy.

---

## 18. Open decisions

Each needs an answer before the relevant simulation.

1. **Market view:** how bullish/bearish/neutral is decided mechanically. Options: a trend
   filter on index closes; the regime switch in the Pulse strategy engine
   ([strategy-engine.md §2.2](strategy-engine.md)); or no view, with fixed moneyness.
2. **Bull vs bear regime** for covered-call adjustment A3.
3. **Covered call long leg:** NIFTY price index, NIFTY total-return index (includes
   dividends the ETF earns), or NIFTYBEES adjusted for the 2019 split.
4. **Strike rules:** keep them as points and rupees, or normalise by % of spot, by
   volatility, or by delta.
5. **Capital base for returns:** notional for the covered call; margin (D10) for
   short-premium strategies. This decides whether D10 is needed at all.
6. **Adjustments in v1:** the covered call is testable without any; most other strategies
   are defined by their adjustments.
7. **Undefined rules:** "dip" and waiting-period puts (§2); level-based monthly strangle
   adjustments (§3); the weekly direction signal (§4).
8. **BANKNIFTY variants:** replay history, port to NIFTY, or drop them.
