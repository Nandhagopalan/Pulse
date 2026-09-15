"""
Covered call on NIFTYBEES, hedged with put structures: explored on 2011-2022,
then run untouched on 2024-2026.

    uv run python -m scripts.covered_call extract --out DIR     # R2 -> local mirror, once
    FNO_MIRROR=DIR uv run python -m scripts.covered_call smoke  # accounting must reconcile first
    FNO_MIRROR=DIR uv run python -m scripts.covered_call sweep  # every config, both windows
    FNO_MIRROR=DIR uv run python -m scripts.covered_call analyse  # picks, walk-forward, finalists

Selection is decided on 2011-2022 alone and written down before the test window
is read: among configs whose drawdown is no worse than buy-and-hold's and which
made money in each of 2011-14, 2015-18 and 2019-22, take the best CAGR divided by
max drawdown. Picking by raw CAGR instead is reported only to show why not: the
top tenth by calibration CAGR landed in the test window's top quarter 3% of the
time, against 35% for the top tenth by CAGR/drawdown.

The position, per lot-equivalent of NIFTY exposure:

    long  NIFTYBEES, sized to the equity's invested fraction
    short one monthly NIFTY call per index unit the ETF holds, at a fixed moneyness
    long  a put structure on the same expiry (butterfly, put, put spread, or none)

and it rolls: every cycle closes the options, rebalances the ETF to the target
weight, and sells the next month. Everything compounds through that rebalance.

What keeps it honest:

- **Traded prices only for trades.** The call is sold only at a strike that
  traded that session. An untraded strike's close is NSE's settlement value and
  misses put-call parity by a median 1.8% of the future, so marks and hedge legs
  that did not trade are priced with Black-76 at the implied volatility of the
  same expiry's traded strikes, never at that close.
- **Expiry settles at intrinsic** against the index close, and exercised long
  legs pay STT on intrinsic value.
- **Costs**: STT on option sales (0.0625% of premium, 0.1% from Oct 2024),
  exchange and SEBI fees with GST, stamp duty on buys, Rs 20 brokerage an order
  (scaled to a Rs 50 lakh book), a half-spread on every fill that widens for
  untraded strikes, and 0.05% impact plus stamp duty on ETF trades.
- **The windows never meet.** Calibration and test each start from cash, so no
  position straddles the 2023 gap, and the test window is never used to choose.

The ETF leg is NIFTYBEES divided by 10 before its 2019-12-19 unit split. Its
ratio to the NIFTY price index rose steadily from 10.06 (2011) to 11.30 (2026),
so dividends stay inside its price and the close is a total return.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import pickle
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import date
from multiprocessing import Pool
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RATE = 0.065                       # for Black-76 discounting and forward fallback only
CAL = (date(2011, 1, 1), date(2022, 12, 31))
TEST = (date(2024, 1, 1), date(2026, 12, 31))
SUBPERIODS = [(date(2011, 1, 1), date(2014, 12, 31)), (date(2015, 1, 1), date(2018, 12, 31)),
              (date(2019, 1, 1), date(2022, 12, 31))]
BEES_SPLIT = date(2019, 12, 19)
NOMINAL_BOOK = 5_000_000.0         # brokerage is per order; this converts it to a fraction of equity
CHARGES_CHANGE = date(2024, 10, 1)
SPREAD_MULT = 1.0                  # stress knob: scales every half-spread paid on option fills


# ─────────────────────────────── pricing ──────────────────────────────────────

def _ncdf_vec(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF. Abramowitz & Stegun 7.1.26 erf, error < 1.5e-7 (no scipy here)."""
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return np.where(x >= 0, 0.5 * (1.0 + erf), 0.5 * (1.0 - erf))


def black76_vec(F, K, T, vol, is_call):
    T = np.maximum(T, 1e-6)
    sd = vol * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    df = np.exp(-RATE * T)
    call = df * (F * _ncdf_vec(d1) - K * _ncdf_vec(d2))
    put = df * (K * _ncdf_vec(-d2) - F * _ncdf_vec(-d1))
    return np.where(is_call, call, put)


def implied_vol_vec(price, F, K, T, is_call, iters: int = 60):
    lo = np.full(price.shape, 0.005)
    hi = np.full(price.shape, 3.0)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        above = black76_vec(F, K, T, mid, is_call) > price
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    iv = 0.5 * (lo + hi)
    intrinsic = np.exp(-RATE * np.maximum(T, 0)) * np.where(is_call, np.maximum(F - K, 0), np.maximum(K - F, 0))
    ok = (T > 0) & np.isfinite(F) & (price > intrinsic + 1e-6) & (iv > 0.006) & (iv < 2.95)
    return np.where(ok, iv, np.nan)


def black76(F: float, K: float, T: float, vol: float, is_call: bool) -> float:
    T = max(T, 1e-6)
    sd = vol * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    df = math.exp(-RATE * T)
    if is_call:
        return df * (F * _ncdf(d1) - K * _ncdf(d2))
    return df * (K * _ncdf(-d2) - F * _ncdf(-d1))


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ─────────────────────────────── data ─────────────────────────────────────────

def extract(out: str) -> None:
    """Copy what the study needs from R2 to local Parquet: NIFTY monthly options near the money, spot, NIFTYBEES."""
    from pipeline.sources import r2
    os.makedirs(out, exist_ok=True)
    con = r2.duck()
    years = [y for y in range(2011, 2027) if y != 2023]
    cf = "[" + ", ".join(f"'s3://pulse-terminal-fno/curated/index_fno_daily/year={y}/data.parquet'" for y in years) + "]"
    uf = "[" + ", ".join(f"'s3://pulse-terminal-fno/curated/index_underlying_daily/year={y}/data.parquet'" for y in years) + "]"
    bf = "[" + ", ".join(f"'s3://pulse-terminal/curated/daily/year={y}/data.parquet'" for y in range(2011, 2027)) + "]"
    t0 = time.time()
    con.execute(f"CREATE TEMP TABLE c AS SELECT * FROM read_parquet({cf}) WHERE symbol = 'NIFTY'")
    con.execute(f"CREATE TEMP TABLE u AS SELECT date, close AS spot, source FROM read_parquet({uf}) WHERE symbol = 'NIFTY'")
    opts = con.execute("""
        WITH monthly AS (SELECT DISTINCT expiry FROM c WHERE instrument = 'FUT'),
             fut AS (SELECT date, expiry, close AS fut, contracts AS fut_contracts FROM c WHERE instrument = 'FUT')
        SELECT o.date, o.expiry, o.strike, CASE o.option_type WHEN 'CE' THEN 'C' ELSE 'P' END AS cp,
               o.close, o.contracts, o.oi, o.lot_size AS lot, f.fut, u.spot
        FROM c o
        JOIN monthly USING (expiry)
        JOIN u USING (date)
        LEFT JOIN fut f USING (date, expiry)
        WHERE o.instrument = 'OPT' AND o.expiry - o.date BETWEEN 0 AND 80
          AND o.strike BETWEEN u.spot * 0.70 AND u.spot * 1.25
    """).df()
    spot = con.execute("SELECT date, spot, source FROM u ORDER BY date").df()
    bees = con.execute(f"SELECT date, close FROM read_parquet({bf}) WHERE symbol = 'NIFTYBEES' ORDER BY date").df()
    print(f"[extract] {len(opts):,} option rows, {len(spot):,} spot rows, {len(bees):,} NIFTYBEES rows "
          f"in {time.time() - t0:.0f}s")

    opts["date"] = pd.to_datetime(opts["date"]).dt.date
    opts["expiry"] = pd.to_datetime(opts["expiry"]).dt.date
    dte = np.array([(e - d).days for d, e in zip(opts["date"], opts["expiry"], strict=False)], dtype=float)
    T = dte / 365.0
    F = opts["fut"].to_numpy(dtype=float)
    S = opts["spot"].to_numpy(dtype=float)
    F = np.where(np.isfinite(F) & (F > 0), F, S * np.exp(RATE * T))
    traded = opts["contracts"].to_numpy() > 0
    iv = implied_vol_vec(opts["close"].to_numpy(dtype=float), F, opts["strike"].to_numpy(dtype=float), T,
                         (opts["cp"] == "C").to_numpy())
    opts["iv"] = np.where(traded, iv, np.nan)
    opts["F"] = F
    print(f"[extract] implied vols: {np.isfinite(opts['iv']).sum():,} of {traded.sum():,} traded rows solved")

    bees["date"] = pd.to_datetime(bees["date"]).dt.date
    bees["bees"] = np.where(bees["date"] < BEES_SPLIT, bees["close"] / 10.0, bees["close"])
    spot["date"] = pd.to_datetime(spot["date"]).dt.date
    spot = spot.merge(bees[["date", "bees"]], on="date", how="left")
    missing = int(spot["bees"].isna().sum())
    spot["bees"] = spot["bees"].ffill()
    print(f"[extract] NIFTYBEES missing on {missing} F&O sessions (carried forward)")

    opts.drop(columns=["spot"]).to_parquet(os.path.join(out, "options.parquet"), index=False)
    spot.to_parquet(os.path.join(out, "spot.parquet"), index=False)
    print(f"[extract] wrote {out}")


@dataclass
class Side:
    strikes: np.ndarray
    close: np.ndarray
    traded: np.ndarray
    iv_strikes: np.ndarray
    iv: np.ndarray


class Market:
    """Indexed option chains, futures, spot and NIFTYBEES for fast per-session lookups."""

    def __init__(self, mirror: str):
        cache = os.path.join(mirror, "market.pkl")
        if os.path.exists(cache):
            with open(cache, "rb") as fh:
                self.__dict__.update(pickle.load(fh))
            return
        opts = pd.read_parquet(os.path.join(mirror, "options.parquet"))
        spot = pd.read_parquet(os.path.join(mirror, "spot.parquet"))
        self.sessions: List[date] = list(spot["date"])
        self.spot: Dict[date, float] = dict(zip(spot["date"], spot["spot"], strict=False))
        self.bees: Dict[date, float] = dict(zip(spot["date"], spot["bees"], strict=False))
        self.chain: Dict[Tuple[date, date], Dict[str, Side]] = {}
        self.fut: Dict[Tuple[date, date], float] = {}
        self.expiries: Dict[date, List[date]] = {}
        opts = opts.sort_values(["date", "expiry", "cp", "strike"])
        for (d, e, cp), g in opts.groupby(["date", "expiry", "cp"], sort=False):
            strikes = g["strike"].to_numpy(dtype=float)
            iv = g["iv"].to_numpy(dtype=float)
            ok = np.isfinite(iv)
            self.chain.setdefault((d, e), {})[cp] = Side(strikes, g["close"].to_numpy(dtype=float),
                                                         g["contracts"].to_numpy() > 0, strikes[ok], iv[ok])
            self.fut[(d, e)] = float(g["F"].iloc[0])
        for d, e in self.chain:
            self.expiries.setdefault(d, []).append(e)
        for d in self.expiries:
            self.expiries[d].sort()
        with open(cache, "wb") as fh:
            pickle.dump(self.__dict__, fh)

    def window(self, start: date, end: date) -> List[date]:
        return [d for d in self.sessions if start <= d <= end and d in self.expiries]

    def price(self, d: date, e: date, strike: float, cp: str) -> Tuple[Optional[float], bool]:
        """(mid price, traded). Expiry day settles at intrinsic; untraded strikes use Black-76 at the smile's IV."""
        S = self.spot[d]
        if d >= e:
            return (max(S - strike, 0.0) if cp == "C" else max(strike - S, 0.0)), True
        q = self.chain.get((d, e))
        if q is None:
            return None, False
        side = q.get(cp)
        if side is not None:
            i = int(np.searchsorted(side.strikes, strike))
            if i < len(side.strikes) and side.strikes[i] == strike and side.traded[i]:
                return float(side.close[i]), True
        T = (e - d).days / 365.0
        F = self.fut.get((d, e), S * math.exp(RATE * T))
        for s in (side, q.get("P" if cp == "C" else "C")):
            if s is not None and len(s.iv) >= 1:
                vol = float(np.interp(strike, s.iv_strikes, s.iv))
                return black76(F, strike, T, vol, cp == "C"), False
        if side is not None:
            i = int(np.searchsorted(side.strikes, strike))
            if i < len(side.strikes) and side.strikes[i] == strike:
                return float(side.close[i]), False
        return None, False

    def nearest(self, d: date, e: date, cp: str, target: float, traded_only: bool,
                tol: Optional[float] = None) -> Optional[float]:
        side = self.chain.get((d, e), {}).get(cp)
        if side is None or not len(side.strikes):
            return None
        strikes = side.strikes[side.traded] if traded_only else side.strikes
        if not len(strikes):
            return None
        k = float(strikes[int(np.argmin(np.abs(strikes - target)))])
        if tol is not None and abs(k - target) > tol:
            return None
        return k

    def listed(self, d: date, e: date, cp: str) -> np.ndarray:
        side = self.chain.get((d, e), {}).get(cp)
        return side.strikes if side is not None else np.array([])


# ─────────────────────────────── strategy ─────────────────────────────────────

@dataclass(frozen=True)
class Config:
    call_m: float = 0.02            # call strike = spot x (1 + call_m) at sale
    target_dte: int = 45            # sell the monthly expiry nearest this many days out
    exit_dte: int = 0               # close and roll at this many days left; 0 = hold to expiry
    hedge: str = "fly"              # none | fly | put | putspread | tail
    body: float = -0.045            # butterfly body / put strike / spread long strike, vs spot
    wing: float = 0.025             # butterfly half-width, or spread width, as a fraction of spot
    hedge_ratio: float = 1.0        # hedge contracts per call contract
    up_roll: Optional[float] = None    # roll the call up when spot exceeds strike by this much
    down_roll: Optional[float] = None  # roll the call down when spot falls this much from the last sale
    coverage: float = 1.0           # fraction of the ETF's index exposure the calls are written on
    take_profit: Optional[float] = None  # buy the call back once it is worth this fraction of its sale price
    buffer: float = 0.06            # equity kept in cash for margin and debits
    tol: float = 0.01               # the call's traded strike must be within this of target

    @property
    def name(self) -> str:
        h = {"none": "nohedge", "fly": f"fly{self.body*100:+.1f}/{self.wing*100:.1f}x{self.hedge_ratio:g}",
             "put": f"put{self.body*100:+.1f}x{self.hedge_ratio:g}",
             "putspread": f"pspr{self.body*100:+.1f}/{self.wing*100:.1f}x{self.hedge_ratio:g}",
             "tail": f"tail{self.body*100:+.1f}x{self.hedge_ratio:g}"}[self.hedge]
        adj = ("" if self.up_roll is None else f"_up{self.up_roll*100:g}") + \
              ("" if self.down_roll is None else f"_dn{self.down_roll*100:g}") + \
              ("" if self.take_profit is None else f"_tp{self.take_profit*100:g}") + \
              ("" if self.coverage == 1.0 else f"_cov{self.coverage*100:g}")
        ex = "exp" if self.exit_dte == 0 else f"x{self.exit_dte}"
        return f"cc{self.call_m*100:+g}_d{self.target_dte}_{ex}_{h}{adj}"


@dataclass
class Leg:
    expiry: date
    strike: float
    cp: str
    qty: float          # signed index units: + long, - short
    role: str
    mark: float = 0.0
    entry: float = 0.0


@dataclass
class Cycle:
    entry: date
    expiry: date
    spot_in: float
    equity_in: float
    call_strike: float
    call_premium: float = 0.0        # rupees per index unit sold, summed over the cycle's sales
    exit: Optional[date] = None
    spot_out: float = 0.0
    equity_out: float = 0.0
    option_pnl: float = 0.0
    hedge_pnl: float = 0.0
    call_pnl: float = 0.0
    adjustments: int = 0
    readded: bool = False
    ref_spot: float = 0.0
    lowest_wing: float = 0.0


class Book:
    def __init__(self, m: Market, cfg: Config):
        self.m, self.cfg = m, cfg
        self.cash = 1.0
        self.units = 0.0
        self.legs: List[Leg] = []
        self.cycle: Optional[Cycle] = None
        self.cycles: List[Cycle] = []
        self.equity_curve: List[Tuple[date, float, float]] = []   # date, equity, etf value
        self.gross_call_premium = 0.0
        self.costs = 0.0
        self.hedge_spend = 0.0
        self.last_equity = 1.0
        self.units_on: Dict[date, float] = {}
        self.etf_costs = 0.0

    # ── fills and charges ──
    def _option_fill(self, d: date, leg: Leg, qty: float, mid: float, traded: bool) -> float:
        """Trade `qty` (signed index units) of `leg`'s contract at `mid` plus a half-spread and charges."""
        half = SPREAD_MULT * (max(0.25, 0.005 * mid) if traded else max(0.5, 0.03 * mid))
        buy = qty > 0
        fill = mid + half if buy else max(mid - half, 0.05)
        turnover = fill * abs(qty)
        new = d >= CHARGES_CHANGE
        exch = turnover * (0.0003503 if new else 0.00053)
        sebi = turnover * 0.000001
        stt = 0.0 if buy else turnover * (0.001 if new else 0.000625)
        stamp = turnover * 0.00003 if buy else 0.0
        brokerage = 20.0 / NOMINAL_BOOK * self.last_equity
        charges = stt + stamp + (exch + sebi + brokerage) * 1.18
        delta = -(fill * qty + charges)
        self.cash += delta
        self._attribute(leg, delta)
        self.costs += charges + half * abs(qty)
        leg.qty += qty
        leg.mark = mid
        return delta

    def _attribute(self, leg: Leg, delta: float) -> None:
        if self.cycle is None:
            return
        if leg.role == "call":
            self.cycle.call_pnl += delta
        else:
            self.cycle.hedge_pnl += delta

    def _etf_trade(self, d: date, value: float) -> None:
        if abs(value) < 1e-12:
            return
        B = self.m.bees[d]
        cost = abs(value) * 0.0005 + (value * 0.00015 if value > 0 else 0.0)
        self.units += value / B
        self.cash -= value + cost
        self.costs += cost
        self.etf_costs += cost

    def equity(self, d: date) -> float:
        opt = 0.0
        for leg in self.legs:
            px, _ = self.m.price(d, leg.expiry, leg.strike, leg.cp)
            if px is not None:
                leg.mark = px
            opt += leg.qty * leg.mark
        return self.cash + self.units * self.m.bees[d] + opt

    # ── cycle management ──
    def _pick_expiry(self, d: date) -> Optional[date]:
        floor = max(self.cfg.exit_dte + 5, 7)
        cands = [e for e in self.m.expiries.get(d, []) if (e - d).days >= floor]
        if not cands:
            return None
        return min(cands, key=lambda e: abs((e - d).days - self.cfg.target_dte))

    def _sell_call(self, d: date, e: date, S: float, qty_units: float) -> Optional[Leg]:
        target = S * (1 + self.cfg.call_m)
        k = self.m.nearest(d, e, "C", target, traded_only=True, tol=self.cfg.tol * S)
        if k is None:
            return None
        mid, traded = self.m.price(d, e, k, "C")
        if mid is None or mid <= 0:
            return None
        leg = Leg(e, k, "C", 0.0, "call", entry=mid)
        self.gross_call_premium += self._option_fill(d, leg, -qty_units, mid, traded)
        self.legs.append(leg)
        if self.cycle:
            self.cycle.call_premium += mid
        return leg

    def _buy_hedge(self, d: date, e: date, S: float, qty_units: float) -> None:
        c = self.cfg
        if c.hedge == "none" or qty_units <= 0:
            return
        listed = self.m.listed(d, e, "P")
        if not len(listed):
            return

        def snap(target: float, avoid: Tuple[float, ...] = ()) -> float:
            order = np.argsort(np.abs(listed - target))
            for i in order:
                if float(listed[i]) not in avoid:
                    return float(listed[i])
            return float(listed[order[0]])

        q = qty_units * c.hedge_ratio
        if c.hedge == "fly":
            body = snap(S * (1 + c.body))
            upper = snap(body + c.wing * S, (body,))
            lower = snap(body - c.wing * S, (body, upper))
            if not lower < body < upper:
                return
            legs = [(upper, q), (body, -2 * q), (lower, q)]
            if self.cycle:
                self.cycle.lowest_wing = lower
        elif c.hedge in ("put", "tail"):
            legs = [(snap(S * (1 + c.body)), q)]
        elif c.hedge == "putspread":
            hi = snap(S * (1 + c.body))
            lo = snap(hi - c.wing * S, (hi,))
            legs = [(hi, q), (lo, -q)]
        else:
            raise ValueError(c.hedge)
        before = self.cash
        for k, qty in legs:
            mid, traded = self.m.price(d, e, k, "P")
            if mid is None:
                continue
            leg = next((x for x in self.legs if x.expiry == e and x.strike == k and x.cp == "P" and x.role == "hedge"), None)
            if leg is None:
                leg = Leg(e, k, "P", 0.0, "hedge")
                self.legs.append(leg)
            self._option_fill(d, leg, qty, max(mid, 0.05), traded)
        self.hedge_spend += before - self.cash

    def _close_all(self, d: date, settle: bool) -> None:
        S = self.m.spot[d]
        cyc = self.cycle
        for leg in self.legs:
            mid, traded = self.m.price(d, leg.expiry, leg.strike, leg.cp)
            if mid is None:
                mid, traded = leg.mark, False
            if settle:
                # Cash settlement at intrinsic; exercised long legs pay 0.125% STT on intrinsic value.
                stt = 0.00125 * mid * leg.qty if leg.qty > 0 and mid > 0 else 0.0
                delta = leg.qty * mid - stt
                self.cash += delta
                self.costs += stt
                self._attribute(leg, delta)
                leg.qty = 0.0
            else:
                self._option_fill(d, leg, -leg.qty, mid, traded)
        self.legs = []
        if cyc is not None:
            cyc.exit, cyc.spot_out = d, S

    def _open_cycle(self, d: date) -> bool:
        e = self._pick_expiry(d)
        if e is None:
            return False
        S, B = self.m.spot[d], self.m.bees[d]
        if self.m.nearest(d, e, "C", S * (1 + self.cfg.call_m), True, self.cfg.tol * S) is None:
            return False
        equity = self.cash + self.units * B
        self._etf_trade(d, (1 - self.cfg.buffer) * equity - self.units * B)
        qty = self.units * B / S
        self.cycle = Cycle(d, e, S, equity, 0.0, ref_spot=S)
        leg = self._sell_call(d, e, S, qty * self.cfg.coverage)
        if leg is None:
            self.cycle = None
            return False
        self.cycle.call_strike = leg.strike
        self._buy_hedge(d, e, S, qty)
        return True

    def _adjust(self, d: date) -> None:
        c, cyc = self.cfg, self.cycle
        if cyc is None:
            return
        S = self.m.spot[d]
        e = cyc.expiry
        if (e - d).days <= c.exit_dte + 2:
            return
        call = next((x for x in self.legs if x.role == "call" and x.qty < 0), None)
        qty = self.units * self.m.bees[d] / S * c.coverage
        if call is not None and c.take_profit is not None:
            mid, traded = self.m.price(d, e, call.strike, "C")
            if mid is not None and mid <= c.take_profit * call.entry:
                self._option_fill(d, call, -call.qty, mid, traded)
                self.legs = [x for x in self.legs if x.qty != 0 or x.role != "call"]
                cyc.adjustments += 1
                return
        if call is not None and c.up_roll is not None and call.strike * (1 + c.up_roll) <= S:
            self._roll_call(d, call, S, qty)
        elif call is not None and c.down_roll is not None and cyc.ref_spot * (1 - c.down_roll) >= S:
            target = self.m.nearest(d, e, "C", S * (1 + c.call_m), True, c.tol * S)
            if target is not None and target < call.strike:
                self._roll_call(d, call, S, qty)

    def _roll_call(self, d: date, call: Leg, S: float, qty: float) -> None:
        cyc = self.cycle
        assert cyc is not None
        mid, traded = self.m.price(d, call.expiry, call.strike, "C")
        if mid is None:
            return
        self._option_fill(d, call, -call.qty, mid, traded)
        self.legs = [x for x in self.legs if x.qty != 0 or x.role != "call"]
        if self.cash < 0:
            self._etf_trade(d, self.cash / (1 + 0.0005))
        new = self._sell_call(d, cyc.expiry, S, qty)
        if new is not None:
            cyc.call_strike = new.strike
        cyc.adjustments += 1
        cyc.ref_spot = S

    def run(self, sessions: List[date]) -> None:
        pending = True
        for d in sessions:
            if self.cycle is not None:
                if d >= self.cycle.expiry:
                    self._finish_cycle(d, settle=True)
                    pending = True
                elif (self.cycle.expiry - d).days <= self.cfg.exit_dte:
                    self._finish_cycle(d, settle=False)
                    pending = True
                else:
                    self._adjust(d)
            if pending and self._open_cycle(d):
                pending = False
            eq = self.equity(d)
            self.last_equity = eq
            self.equity_curve.append((d, eq, self.units * self.m.bees[d]))
            self.units_on[d] = self.units
        if self.cycle is not None:
            self._finish_cycle(sessions[-1], settle=False, final=True)

    def _finish_cycle(self, d: date, settle: bool, final: bool = False) -> None:
        cyc = self.cycle
        self._close_all(d, settle)
        if self.cash < 0:
            self._etf_trade(d, self.cash / (1 + 0.0005))
        if cyc is not None:
            cyc.equity_out = self.cash + self.units * self.m.bees[d]
            cyc.option_pnl = cyc.call_pnl + cyc.hedge_pnl
            self.cycles.append(cyc)
        self.cycle = None


# ─────────────────────────────── metrics ──────────────────────────────────────

def _curve_stats(dates: List[date], eq: np.ndarray) -> dict:
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1
    rets = eq[1:] / eq[:-1] - 1
    per_year = len(rets) / years
    vol = float(np.std(rets, ddof=1) * math.sqrt(per_year)) if len(rets) > 2 else float("nan")
    peak = np.maximum.accumulate(eq)
    dd = float(np.min(eq / peak - 1))
    return {"cagr": cagr, "vol": vol, "max_dd": dd, "calmar": cagr / abs(dd) if dd < 0 else float("nan"),
            "ret_over_vol": cagr / vol if vol and vol > 0 else float("nan"), "total": eq[-1] / eq[0] - 1,
            "years": years}


def evaluate(m: Market, cfg: Config, start: date, end: date, detail: bool = False) -> dict:
    sessions = m.window(start, end)
    book = Book(m, cfg)
    book.run(sessions)
    dates = [x[0] for x in book.equity_curve]
    eq = np.array([x[1] for x in book.equity_curve])
    etf = np.array([x[2] for x in book.equity_curve])
    bench = np.array([m.bees[d] for d in dates]) / m.bees[dates[0]]
    out = {"name": cfg.name, "config": asdict(cfg), "start": str(dates[0]), "end": str(dates[-1])}
    s = _curve_stats(dates, eq)
    b = _curve_stats(dates, bench)
    years = s["years"]
    avg_equity = float(np.mean(eq))
    avg_etf = float(np.mean(etf))
    call_income = sum(c.call_pnl for c in book.cycles)
    hedge_result = sum(c.hedge_pnl for c in book.cycles)
    out.update({
        **s,
        "bench_cagr": b["cagr"], "bench_max_dd": b["max_dd"], "bench_vol": b["vol"],
        # Return on assets: average annual profit over the average capital employed (ETF + cash).
        "roa": (eq[-1] - eq[0]) / years / avg_equity,
        # Written yield: premium sold per year over the average ETF value it was written against.
        "written_yield": book.gross_call_premium / years / avg_etf,
        "call_pnl_yield": call_income / years / avg_etf,
        "hedge_yield": hedge_result / years / avg_etf,
        "cost_yield": book.costs / years / avg_equity,
        "cycles": len(book.cycles),
        "cycle_win_rate": float(np.mean([c.equity_out > c.equity_in for c in book.cycles])) if book.cycles else float("nan"),
        "adjustments": sum(c.adjustments for c in book.cycles),
    })
    for i, (a, z) in enumerate(SUBPERIODS):
        idx = [j for j, d in enumerate(dates) if a <= d <= z]
        if len(idx) > 20:
            ss = _curve_stats([dates[j] for j in idx], eq[idx])
            bb = _curve_stats([dates[j] for j in idx], bench[idx])
            out[f"sub{i}_cagr"], out[f"sub{i}_dd"], out[f"sub{i}_bench"] = ss["cagr"], ss["max_dd"], bb["cagr"]
    if detail:
        out["curve"] = [(str(d), float(e), float(bb)) for d, e, bb in zip(dates, eq, bench, strict=False)]
        yearly = {}
        for y in sorted({d.year for d in dates}):
            idx = [j for j, d in enumerate(dates) if d.year == y]
            first = idx[0] - 1 if idx[0] > 0 else idx[0]
            yearly[y] = {"strategy": float(eq[idx[-1]] / eq[first] - 1), "bench": float(bench[idx[-1]] / bench[first] - 1)}
        out["yearly"] = yearly
        out["cycle_rows"] = [{
            "entry": str(c.entry), "exit": str(c.exit), "expiry": str(c.expiry),
            "spot_in": c.spot_in, "spot_out": c.spot_out, "call_strike": c.call_strike,
            "call_premium_pts": c.call_premium, "ret": c.equity_out / c.equity_in - 1,
            "call_pnl_pct": c.call_pnl / c.equity_in, "hedge_pnl_pct": c.hedge_pnl / c.equity_in,
            "adjustments": c.adjustments} for c in book.cycles]
    return out


# ─────────────────────────────── sweep ────────────────────────────────────────

def grid() -> List[Config]:
    hedges = [
        {"hedge": "none"},
        {"hedge": "fly", "body": -0.045, "wing": 0.025, "hedge_ratio": 1.0},
        {"hedge": "fly", "body": -0.03, "wing": 0.02, "hedge_ratio": 1.0},
        {"hedge": "fly", "body": -0.06, "wing": 0.03, "hedge_ratio": 1.0},
        {"hedge": "fly", "body": -0.045, "wing": 0.025, "hedge_ratio": 2.0},
        {"hedge": "put", "body": -0.05, "hedge_ratio": 1.0},
        {"hedge": "putspread", "body": -0.03, "wing": 0.05, "hedge_ratio": 1.0},
        {"hedge": "tail", "body": -0.10, "hedge_ratio": 1.0},
    ]
    out = []
    for call_m, dte, exit_dte, h, up, dn, tp, cov in itertools.product(
            [-0.01, 0.0, 0.01, 0.02, 0.03, 0.05, 0.07], [30, 45], [0, 10, 20], hedges,
            [None, 0.02], [None, 0.04], [None, 0.2], [1.0, 0.5]):
        if exit_dte >= dte - 7:
            continue
        out.append(Config(call_m=call_m, target_dte=dte, exit_dte=exit_dte, up_roll=up, down_roll=dn,
                          take_profit=tp, coverage=cov, **h))
    return out


_MARKET: Optional[Market] = None


def _init(mirror: str) -> None:
    global _MARKET
    _MARKET = Market(mirror)


def _run_one(cfg: Config) -> dict:
    assert _MARKET is not None
    cal = evaluate(_MARKET, cfg, *CAL)
    test = evaluate(_MARKET, cfg, *TEST)
    return {"name": cfg.name, "config": asdict(cfg),
            "cal": {k: v for k, v in cal.items() if k not in ("config", "name")},
            "test": {k: v for k, v in test.items() if k not in ("config", "name")}}


def sweep(mirror: str) -> None:
    Market(mirror)  # build the cache once before forking workers
    configs = grid()
    t0 = time.time()
    print(f"[sweep] {len(configs)} configs on {os.cpu_count()} cores")
    with Pool(os.cpu_count(), initializer=_init, initargs=(mirror,)) as pool:
        results = []
        for i, r in enumerate(pool.imap_unordered(_run_one, configs, chunksize=4), 1):
            results.append(r)
            if i % 100 == 0:
                print(f"[sweep] {i}/{len(configs)} in {time.time() - t0:.0f}s", flush=True)
    path = os.path.join(mirror, "sweep.json")
    with open(path, "w") as fh:
        json.dump(results, fh)
    print(f"[sweep] {len(results)} results -> {path} in {time.time() - t0:.0f}s")


def smoke(m: Market) -> None:
    """Accounting must reconcile before any result is believed, then time two configs."""
    sessions = m.window(*CAL)
    for cfg in (Config(hedge="none", call_m=0.02), Config(), Config(up_roll=0.02, down_roll=0.04)):
        t0 = time.time()
        book = Book(m, cfg)
        # Track the ETF's own P&L day by day so the identity below is exact.
        etf_pnl = 0.0
        prev_units, prev_d = 0.0, None
        book_run_days = []
        original_etf_trade = book._etf_trade

        def etf_trade(d, value, _orig=original_etf_trade):
            _orig(d, value)
        book._etf_trade = etf_trade  # type: ignore[method-assign]
        book.run(sessions)
        for (d, _eq, _v) in book.equity_curve:
            if prev_d is not None:
                etf_pnl += prev_units * (m.bees[d] - m.bees[prev_d])
            prev_units, prev_d = book.units_on.get(d, prev_units), d
            book_run_days.append(d)
        option_cash = sum(c.call_pnl + c.hedge_pnl for c in book.cycles)
        final = book.equity_curve[-1][1]
        etf_costs = book.etf_costs
        explained = 1.0 + etf_pnl + option_cash - etf_costs
        gap = final - explained
        r = evaluate(m, cfg, *CAL)
        print(f"{cfg.name}")
        print(f"   final equity {final:.6f} = 1 + ETF P&L {etf_pnl:+.6f} + option cash {option_cash:+.6f} "
              f"- ETF costs {etf_costs:.6f}  (unexplained {gap:+.2e})")
        print(f"   CAGR {r['cagr']:.2%}  maxDD {r['max_dd']:.2%}  bench CAGR {r['bench_cagr']:.2%} DD {r['bench_max_dd']:.2%}  "
              f"written {r['written_yield']:.2%}  call P&L {r['call_pnl_yield']:+.2%}  hedge {r['hedge_yield']:+.2%}  "
              f"costs {r['cost_yield']:.2%}  cycles {r['cycles']}  adj {r['adjustments']}  ({time.time() - t0:.1f}s)")


PLAYBOOK = Config(call_m=0.02, target_dte=45, exit_dte=20, hedge="fly", body=-0.045, wing=0.025)
NEIGHBOURS = {"call_m": [-0.01, 0.0, 0.01, 0.02, 0.03, 0.05, 0.07], "target_dte": [30, 45], "exit_dte": [0, 10, 20],
              "up_roll": [None, 0.02], "down_roll": [None, 0.04], "take_profit": [None, 0.2], "coverage": [1.0, 0.5]}


def _eligible(r: dict) -> bool:
    c = r["cal"]
    return c["max_dd"] >= c["bench_max_dd"] and all(c.get(f"sub{i}_cagr", -1.0) > 0 for i in range(3))


def _worst_episode(curve: List[Tuple[str, float, float]], col: int) -> dict:
    dates = [c[0] for c in curve]
    v = np.array([c[col] for c in curve])
    dd = v / np.maximum.accumulate(v) - 1
    i = int(np.argmin(dd))
    j = int(np.argmax(v[: i + 1]))
    rec = next((k for k in range(i, len(v)) if v[k] >= v[j]), None)
    return {"dd": float(dd[i]), "peak": dates[j], "trough": dates[i], "recovered": dates[rec] if rec is not None else None}


def analyse(mirror: str) -> None:
    global SPREAD_MULT
    with open(os.path.join(mirror, "sweep.json")) as fh:
        R = json.load(fh)
    by_name = {r["name"]: r for r in R}
    cal0, test0 = R[0]["cal"], R[0]["test"]
    out: dict = {"bench": {
        "cal": {"cagr": cal0["bench_cagr"], "max_dd": cal0["bench_max_dd"], "vol": cal0["bench_vol"],
                "sub": [cal0[f"sub{i}_bench"] for i in range(3)]},
        "test": {"cagr": test0["bench_cagr"], "max_dd": test0["bench_max_dd"], "vol": test0["bench_vol"]}}}

    cc_ = np.array([r["cal"]["cagr"] for r in R])
    tc = np.array([r["test"]["cagr"] for r in R])
    cm = np.array([r["cal"]["calmar"] for r in R])
    tm = np.array([r["test"]["calmar"] for r in R])
    rank = lambda a: np.argsort(np.argsort(a))  # noqa: E731
    top_cm, top_cc = cm >= np.quantile(cm, 0.9), cc_ >= np.quantile(cc_, 0.9)
    out["grid"] = {
        "configs": len(R),
        "cal": {"median_cagr": float(np.median(cc_)), "best_cagr": float(cc_.max()),
                "share_beating_bench": float(np.mean(cc_ > cal0["bench_cagr"]))},
        "test": {"median_cagr": float(np.median(tc)), "best_cagr": float(tc.max()),
                 "share_beating_bench": float(np.mean(tc > test0["bench_cagr"]))},
        "rank_corr_cagr": float(np.corrcoef(rank(cc_), rank(tc))[0, 1]),
        "rank_corr_calmar": float(np.corrcoef(rank(cm), rank(tm))[0, 1]),
        "top_decile_calmar_stays_top_quarter_in_test": float(np.mean(tm[top_cm] >= np.quantile(tm, 0.75))),
        "top_decile_cagr_stays_top_quarter_in_test": float(np.mean(tc[top_cc] >= np.quantile(tc, 0.75))),
        "top_decile_calmar_hedge_mix": {h: float(np.mean([R[i]["config"]["hedge"] == h for i in np.where(top_cm)[0]]))
                                        for h in ("put", "tail", "putspread", "fly", "none")},
        "by_setting": {k: {str(v): {"median_cagr": float(np.median([r["cal"]["cagr"] for r in R if r["config"][k] == v])),
                                    "median_dd": float(np.median([r["cal"]["max_dd"] for r in R if r["config"][k] == v]))}
                           for v in vals} for k, vals in NEIGHBOURS.items()},
        "by_hedge": {},
    }
    for r in R:
        h = r["name"].split("_")[3]
        out["grid"]["by_hedge"].setdefault(h, []).append((r["cal"]["cagr"], r["cal"]["max_dd"], r["cal"]["calmar"]))
    out["grid"]["by_hedge"] = {h: {"median_cagr": float(np.median([x[0] for x in v])), "median_dd": float(np.median([x[1] for x in v])),
                                   "median_calmar": float(np.median([x[2] for x in v]))} for h, v in out["grid"]["by_hedge"].items()}

    def sub_calmar(r: dict, i: int) -> float:
        d = r["cal"][f"sub{i}_dd"]
        return r["cal"][f"sub{i}_cagr"] / abs(d) if d < 0 else float("nan")
    wf = {}
    for a, b in ((0, 1), (1, 2)):
        va = np.array([sub_calmar(r, a) for r in R])
        vb = np.array([sub_calmar(r, b) for r in R])
        wf[f"{a}->{b}"] = float(np.mean(vb[va >= np.nanquantile(va, 0.9)] >= np.nanquantile(vb, 0.75)))
    pick15 = max(R, key=lambda r: sub_calmar(r, 1))
    wf["picked_on_2015_18"] = {"name": pick15["name"],
                               "test_rank": int((tm > pick15["test"]["calmar"]).sum()) + 1}
    out["walkforward"] = wf

    elig = [r for r in R if _eligible(r)]
    picks = {
        "F1": max(elig, key=lambda r: r["cal"]["calmar"]),
        "F2": max((r for r in elig if r["config"]["hedge"] == "fly"), key=lambda r: r["cal"]["calmar"]),
        "F3": max((r for r in elig if r["config"]["hedge"] == "put" and r["config"]["coverage"] == 1.0),
                  key=lambda r: r["cal"]["calmar"]),
        "A": max(elig, key=lambda r: r["cal"]["cagr"]),
    }
    out["eligible"] = len(elig)
    configs = {"F1": Config(**picks["F1"]["config"]), "F2": Config(**picks["F2"]["config"]),
               "F0": PLAYBOOK, "F3": Config(**picks["F3"]["config"]), "A": Config(**picks["A"]["config"])}
    order = np.argsort(-tm)
    test_rank = {R[i]["name"]: n for n, i in enumerate(order, 1)}

    m = Market(mirror)
    out["finalists"] = {}
    for label, cfg in configs.items():
        f: dict = {"name": cfg.name, "config": asdict(cfg), "test_rank_calmar": test_rank.get(cfg.name)}
        for w, win in (("cal", CAL), ("test", TEST)):
            r = evaluate(m, cfg, *win, detail=True)
            curve = r.pop("curve")
            rets = [c["ret"] for c in r["cycle_rows"]]
            days = [(date.fromisoformat(c["exit"]) - date.fromisoformat(c["entry"])).days for c in r["cycle_rows"]]
            r["cycle_stats"] = {"avg": float(np.mean(rets)), "median": float(np.median(rets)), "worst": float(min(rets)),
                                "best": float(max(rets)), "avg_days": float(np.mean(days))}
            r["episode"] = _worst_episode(curve, 1)
            r["bench_episode"] = _worst_episode(curve, 2)
            r["rs50l"] = {"strategy": 50 * (1 + r["cagr"]) ** r["years"], "bench": 50 * (1 + r["bench_cagr"]) ** r["years"]}
            r["curve"] = curve
            r.pop("cycle_rows")
            f[w] = r
        stress = {}
        for tag in ("spread_x2", "buffer_10"):
            SPREAD_MULT = 2.0 if tag == "spread_x2" else 1.0
            c2 = replace(cfg, buffer=0.10) if tag == "buffer_10" else cfg
            stress[tag] = {w: {k: evaluate(m, c2, *win)[k] for k in ("cagr", "max_dd")} for w, win in (("cal", CAL), ("test", TEST))}
        SPREAD_MULT = 1.0
        f["stress"] = stress
        f["neighbours"] = []
        for k, vals in NEIGHBOURS.items():
            for v in vals:
                if getattr(cfg, k) == v:
                    continue
                n = by_name.get(replace(cfg, **{k: v}).name)
                if n:
                    f["neighbours"].append({"change": f"{k}={v}", "cal_cagr": n["cal"]["cagr"], "cal_dd": n["cal"]["max_dd"],
                                            "test_cagr": n["test"]["cagr"], "test_dd": n["test"]["max_dd"]})
        out["finalists"][label] = f
        c, t = f["cal"], f["test"]
        print(f"{label} {cfg.name:46} CAL {c['cagr']:.2%}/{c['max_dd']:.2%}  TEST {t['cagr']:.2%}/{t['max_dd']:.2%}  "
              f"ROA {c['roa']:.2%}/{t['roa']:.2%}  written {c['written_yield']:.2%}/{t['written_yield']:.2%}")
    path = os.path.join(mirror, "analysis.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, default=str)
    os.replace(tmp, path)
    g = out["grid"]
    print(f"bench CAL {out['bench']['cal']['cagr']:.2%}/{out['bench']['cal']['max_dd']:.2%}  TEST {out['bench']['test']['cagr']:.2%}/{out['bench']['test']['max_dd']:.2%}")
    print(f"grid {g['configs']}: beat B&H CAL {g['cal']['share_beating_bench']:.1%} TEST {g['test']['share_beating_bench']:.1%}; "
          f"rank corr CAGR {g['rank_corr_cagr']:.2f} calmar {g['rank_corr_calmar']:.2f}; top-decile persistence "
          f"calmar {g['top_decile_calmar_stays_top_quarter_in_test']:.0%} cagr {g['top_decile_cagr_stays_top_quarter_in_test']:.0%}")
    print(f"walk-forward {wf}")
    print(f"wrote {path}")


def main(argv: List[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    cmd = argv[0]
    if cmd == "extract":
        extract(argv[argv.index("--out") + 1])
        return
    mirror = os.environ.get("FNO_MIRROR")
    if not mirror:
        raise SystemExit("set FNO_MIRROR to the directory written by `extract`")
    if cmd == "sweep":
        sweep(mirror)
    elif cmd == "analyse":
        analyse(mirror)
    elif cmd == "smoke":
        smoke(Market(mirror))
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    # Run as the importable module, so the pickled market cache and the pool's
    # workers resolve classes by their real name rather than as __main__.
    from scripts import covered_call as _module
    _module.main(sys.argv[1:])
