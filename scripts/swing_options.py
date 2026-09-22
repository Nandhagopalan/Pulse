"""
The swing book, with stock options standing in for shares where they can.

    uv run python -m scripts.swing_options extract --out DIR [--years 2011-2026]
    FNO_MIRROR=DIR PULSE_BARS_PKL=FILE uv run python -m scripts.swing_options trades
    FNO_MIRROR=DIR uv run python -m scripts.swing_options smoke
    FNO_MIRROR=DIR uv run python -m scripts.swing_options sweep
    FNO_MIRROR=DIR uv run python -m scripts.swing_options analyse

The share book is left exactly as it trades. Only the trades whose stock had
listed options on the entry date are candidates, and each is either expressed
as an option position or, when no option on it traded that session, kept in
shares. Everything else in the book, the regime exit, the stops, the trail,
which names are bought and when, is unchanged; the question is only whether the
same decisions are better carried by a call than by the stock.

The substitution is done on the book's daily P&L rather than inside the engine:
a substituted trade's share P&L (and its charges) is taken out of each session
and the option position's is put in, both as fractions of that session's book
equity, and the result is chain-linked. Sizing therefore compounds as the share
book's does, and cash the option does not use earns the preset's idle-cash yield
exactly as it would have.

Timing. Both books act on a signal made at a session's close. Shares fill at the
next open, as the engine does. Options have only end-of-day prices here, so the
option opens and closes at the close of the fill session, one part-session
later than the stock. To keep that from being mistaken for an instrument effect,
a third book, shares at those same closes, is reported beside the other two.

Pricing rules are the covered-call study's, tightened for stock options, whose
thin strikes print nonsense (a higher strike closing above a lower one on 2 and
18 contracts): an option is bought, sold or rolled only at a strike with at least
LIQUID contracts that session; marks for other strikes come from Black-76 at the
smile of the liquid strikes; fills pay a half-spread of the premium, tripled on
an illiquid strike, and the stress runs scale it. A roll that finds no liquid
strike carries the rest of the trade in shares, as a trader would. A bonus or
split is detected where the raw future and the adjusted close part by over 15%,
and open legs are rescaled the way NSE rescales them.

Two controls sit beside every result: the same trades in shares at the options'
closes (timing), and the book with those trades not taken at all. The second
matters: a thin out-of-the-money call "improves" the book mostly by carrying less
of trades that lost, and skipping them does the same thing more cheaply.

Selection is decided on 2011-2022 and written down here before the test window
is read: the highest Calmar of the hybrid book, among configs that substituted at
least 100 trades and did not lower CAGR in any of 2011-14, 2015-18 and 2019-22
against the share book. The test window, 2024 to date, is then read once.
"""
from __future__ import annotations

import io
import itertools
import math
import os
import pickle
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.config import RAW_FO_BHAV_PREFIX
from pipeline.sources import nse, r2

from scripts.covered_call import black76, implied_vol_vec

FNO_BUCKET = "pulse-terminal-fno"
STOCK_FO_KINDS = {"FUTSTK": "FUT", "OPTSTK": "OPT", "STF": "FUT", "STO": "OPT"}

RATE = 0.065                   # forward fallback only, when an expiry's future is missing
PRESETS = ("deployed", "balanced")
OPT_FROM = "2011-01-01"        # first session with option chains in the mirror
CAL = ("2011-01-01", "2023-01-01")
TEST = ("2024-01-01", None)
SUBPERIODS = [("2011-01-01", "2015-01-01"), ("2015-01-01", "2019-01-01"), ("2019-01-01", "2023-01-01")]
NOMINAL_BOOK = 5_000_000.0     # brokerage is Rs 20 an order on a book this size, scaled with equity
CHARGES_CHANGE = np.datetime64("2024-10-01")
MIN_SUBSTITUTED = 100
# A strike counts as traded only with at least this many contracts that session.
# Below it, a stock option's close is often one odd lot: on 2024-12-18 BSE's
# January 5500 call closed above its 5400 call on 18 and 2 contracts.
LIQUID = 25


# ─────────────────────────────── data ─────────────────────────────────────────

def _stock_rows(key: str) -> pd.DataFrame:
    """One session's stock futures (all) and stock options (traded only), in the lake's row shape."""
    d = date.fromisoformat(key.rsplit("/", 1)[1][:10])
    blob = r2.get_object(key, FNO_BUCKET)
    if blob is None:
        return pd.DataFrame()
    # Every symbol: the parser filters by name, so hand it the file's own.
    z = zipfile.ZipFile(io.BytesIO(blob))
    raw = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
    col = next(c for c in raw.columns if c.strip().upper() in ("SYMBOL", "TCKRSYMB"))
    rows = nse.parse_fo_bhavcopy(blob, d, set(raw[col].dropna().str.strip().str.upper()))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    keep = (df["instrument"] == "FUT") | (df["contracts"] > 0)
    cols = ["symbol", "date", "instrument", "expiry", "strike", "option_type",
            "close", "settle", "contracts", "oi", "turnover", "lot_size"]
    return df.loc[keep, cols]


def extract(out: str, years: List[int]) -> None:
    """R2 raw bhavcopies -> DIR/stock_fo/YYYY.parquet: stock futures, and stock options that traded."""
    os.makedirs(os.path.join(out, "stock_fo"), exist_ok=True)
    for y in years:
        dest = os.path.join(out, "stock_fo", f"{y}.parquet")
        if os.path.exists(dest):
            print(f"[extract] {y} present", flush=True)
            continue
        keys = sorted(r2.list_keys(f"{RAW_FO_BHAV_PREFIX}/{y}/", FNO_BUCKET))
        if not keys:
            print(f"[extract] {y}: no raw files", flush=True)
            continue
        # Patched once around the pool: patching per thread would race.
        with mock.patch.dict(nse.INDEX_FO_KINDS, STOCK_FO_KINDS, clear=True), ThreadPoolExecutor(12) as ex:
            parts = [p for p in ex.map(_stock_rows, keys) if not p.empty]
        df = pd.concat(parts, ignore_index=True)
        df.to_parquet(dest, index=False)
        opt = df[df.instrument == "OPT"]
        print(f"[extract] {y}: {len(keys)} sessions, {df.symbol.nunique()} stocks, "
              f"{len(opt):,} traded option rows", flush=True)



# ─────────────────────────────── chains ───────────────────────────────────────

@dataclass
class Side:
    strikes: np.ndarray
    close: np.ndarray
    contracts: np.ndarray
    iv: np.ndarray


class Chains:
    """
    Stock option chains for only the (symbol, session) pairs a trade can touch.

    Each expiry carries its future's close, which is the forward the options are
    priced against: it already holds the carry and any dividend the market
    expects, so no dividend model is needed.
    """

    def __init__(self, mirror: str, needs: Dict[str, List[Tuple[np.datetime64, np.datetime64]]]):
        self.fut: Dict[Tuple[str, np.datetime64], Dict[np.datetime64, float]] = {}
        self.lot: Dict[Tuple[str, np.datetime64], int] = {}
        self.chain: Dict[Tuple[str, np.datetime64, np.datetime64], Dict[str, Side]] = {}
        frames = []
        for f in sorted(os.listdir(os.path.join(mirror, "stock_fo"))):
            df = pd.read_parquet(os.path.join(mirror, "stock_fo", f))
            df = df[df.symbol.isin(needs)]
            keep = np.zeros(len(df), bool)
            sym, dts = df.symbol.to_numpy(), pd.to_datetime(df["date"]).to_numpy().astype("datetime64[D]")
            for s_, spans in needs.items():
                m = sym == s_
                for a, b in spans:
                    keep |= m & (dts >= a) & (dts <= b)
            frames.append(df[keep])
        df = pd.concat(frames, ignore_index=True)
        # Pandas stores these at second resolution; every key and every day count
        # below must be in days, or an expiry a month out reads as 2.6 million.
        day = lambda col: df[col].to_numpy().astype("datetime64[D]")  # noqa: E731
        df_d, df_e = day("date"), day("expiry")
        fut = df.instrument.to_numpy() == "FUT"
        for s_, d, e, c in zip(df.symbol.to_numpy()[fut], df_d[fut], df_e[fut],
                               df.close.to_numpy(float)[fut], strict=True):
            self.fut.setdefault((s_, d), {})[e] = float(c)
        opt = df[~fut].copy()
        o_d, o_e = df_d[~fut], df_e[~fut]
        F = np.array([self.forward(s_, d, e) for s_, d, e in zip(opt.symbol, o_d, o_e, strict=True)])
        T = (o_e - o_d).astype(float) / 365.0
        opt["iv"] = implied_vol_vec(opt.close.to_numpy(float), F, opt.strike.to_numpy(float), T,
                                    (opt.option_type == "CE").to_numpy())
        opt["d"], opt["e"] = o_d, o_e
        for (s_, e), g in opt.dropna(subset=["lot_size"]).groupby(["symbol", "e"]):
            self.lot[(s_, np.datetime64(e, "D"))] = int(g.lot_size.median())
        opt = opt.sort_values(["symbol", "d", "e", "option_type", "strike"])
        for (s_, d, e, cp), g in opt.groupby(["symbol", "d", "e", "option_type"], sort=False):
            d, e = np.datetime64(d, "D"), np.datetime64(e, "D")
            self.chain.setdefault((s_, d, e), {})[cp[0]] = Side(
                g.strike.to_numpy(float), g.close.to_numpy(float), g.contracts.to_numpy(), g.iv.to_numpy(float))

    def expiries(self, sym: str, d: np.datetime64) -> List[np.datetime64]:
        return sorted(self.fut.get((sym, d), {}))

    def forward(self, sym: str, d: np.datetime64, e: np.datetime64) -> float:
        f = self.fut.get((sym, d), {})
        if e in f:
            return float(f[e])
        if not f:
            return float("nan")
        e0 = min(f)
        return float(f[e0]) * math.exp(RATE * (e - e0).astype(float) / 365.0)

    def traded_strike(self, sym: str, d, e, cp: str, target: float, tol: float) -> Optional[float]:
        side = self.chain.get((sym, d, e), {}).get(cp)
        if side is None:
            return None
        liquid = side.contracts >= LIQUID
        if not liquid.any():
            return None
        strikes = side.strikes[liquid]
        k = float(strikes[int(np.argmin(np.abs(strikes - target)))])
        return k if abs(k - target) <= tol else None

    def price(self, sym: str, d, e, K: float, cp: str, iv_hint: float) -> Tuple[float, bool, float]:
        """(mid, traded, iv). A strike without LIQUID contracts is priced by Black-76 at the liquid strikes' smile."""
        F = self.forward(sym, d, e)
        if d >= e:
            return max(F - K, 0.0) if cp == "C" else max(K - F, 0.0), True, iv_hint
        q = self.chain.get((sym, d, e), {})
        side = q.get(cp)
        if side is not None:
            i = int(np.argmin(np.abs(side.strikes - K)))
            if abs(side.strikes[i] - K) < 0.011 and side.contracts[i] >= LIQUID:  # adjusted strikes print to the paisa
                iv = side.iv[i] if np.isfinite(side.iv[i]) else iv_hint
                return float(side.close[i]), True, float(iv)
        vol = iv_hint
        for s_ in (side, q.get("P" if cp == "C" else "C")):
            if s_ is not None:
                ok = np.isfinite(s_.iv) & (s_.contracts >= LIQUID)
                if ok.any():
                    vol = float(np.interp(K, s_.strikes[ok], s_.iv[ok]))
                    break
        if not np.isfinite(F) or not np.isfinite(vol):
            return float("nan"), False, vol
        return black76(F, K, (e - d).astype(float) / 365.0, vol, cp == "C"), False, vol


# ─────────────────────────────── trades ───────────────────────────────────────

def trades(out: str) -> None:
    """
    Run the share book for each preset and keep what the substitution needs.

    Per trade: the adjusted open and close path from entry to exit, so its daily
    share P&L can be rebuilt exactly as the engine booked it, and whether the
    stock had a future listed on the entry session.
    """
    from pipeline.compute.strategy import backtest, data, rules
    from pipeline.compute.strategy.config import preset

    pkl = os.environ.get("PULSE_BARS_PKL")
    if pkl and os.path.exists(pkl):
        with open(pkl, "rb") as fh:
            md = pickle.load(fh)
    else:
        md = data.from_lake(r2.duck("8GB"))
    listed: Dict[np.datetime64, set] = {}
    for f in sorted(os.listdir(os.path.join(out, "stock_fo"))):
        fut = pd.read_parquet(os.path.join(out, "stock_fo", f), columns=["symbol", "date", "instrument"])
        fut = fut[fut.instrument == "FUT"]
        for d, g in fut.groupby("date"):
            listed[np.datetime64(d, "D")] = set(g.symbol)
    for name in PRESETS:
        cfg = preset(name)
        res = backtest.run(md, rules.compute_features(md, cfg), cfg)
        rows = []
        for p in res.trades:
            t0 = int(np.searchsorted(md.dates, p.entry_date))
            t1 = int(np.searchsorted(md.dates, p.exit_date))
            closes = md.close[t0:t1 + 1, p.col].astype(float)
            rows.append({
                "symbol": p.symbol, "t0": t0, "t1": t1, "entry_date": p.entry_date, "exit_date": p.exit_date,
                "entry": p.entry, "exit": p.exit, "qty": p.qty, "init_stop": p.init_stop, "pnl": p.pnl, "ret": p.ret,
                "bars": p.bars, "reason": p.reason, "closes": closes,
                "fno": p.symbol in listed.get(p.entry_date, set()),
                "has_chain": p.entry_date in listed,
            })
        book = {"dates": res.dates, "equity": res.equity, "twr": res.twr, "invested": res.invested,
                    "all_dates": md.dates, "cfg": asdict(cfg), "trades": rows}
        with open(os.path.join(out, f"book_{name}.pkl"), "wb") as fh:
            pickle.dump(book, fh)
        s = backtest.summarise(res)
        n_f = sum(r["fno"] for r in rows)
        print(f"[trades] {name}: CAGR {s['cagr']:.2%}, DD {s['max_dd']:.2%}, {len(rows)} trades, "
              f"{n_f} with a listed future at entry", flush=True)


# ─────────────────────────────── the option leg ───────────────────────────────

@dataclass(frozen=True)
class Config:
    preset: str = "deployed"
    instrument: str = "call"       # call | spread | shares_close (timing control) | skip (not traded)
    moneyness: float = 0.0         # strike / forward - 1; -0.05 is 5% in the money
    width: float = 0.10            # spread: short strike this far above the long one
    min_dte: int = 20              # first monthly expiry at least this many days out
    roll_dte: int = 5              # roll when an open position gets this close to expiry
    sizing: str = "units"          # units: x shares' count | risk: premium = x * the trade's 1R
    size: float = 1.0
    half_spread: float = 0.015     # of premium, on a strike that traded; x3 when it did not
    strike_tol: float = 0.03       # nearest traded strike must be within this of the target

    @property
    def name(self) -> str:
        if self.instrument == "shares_close":
            return f"{self.preset}/shares@close"
        if self.instrument == "skip":
            return f"{self.preset}/F&O trades skipped"
        leg = "C" if self.instrument == "call" else f"CS{self.width:.0%}"
        return (f"{self.preset}/{leg} m{self.moneyness:+.0%} dte{self.min_dte} "
                f"{self.sizing}x{self.size:g} hs{self.half_spread:.1%}")


@dataclass
class Leg:
    expiry: np.datetime64
    strike: float
    qty: float                     # signed share units
    mark: float = 0.0
    iv: float = 0.35


def _charges(d: np.datetime64, turnover: float, buy: bool, equity_scale: float) -> float:
    new = d >= CHARGES_CHANGE
    exch = turnover * (0.0003503 if new else 0.00053)
    sebi = turnover * 0.000001
    stt = 0.0 if buy else turnover * (0.001 if new else 0.000625)
    stamp = turnover * 0.00003 if buy else 0.0
    brokerage = 20.0 * equity_scale
    return stt + stamp + (exch + sebi + brokerage) * 1.18


class OptionTrade:
    """One substituted trade: its option legs, cash and marks, session by session."""

    def __init__(self, ch: Chains, cfg: Config, sym: str, equity_scale: float,
                 book_cfg: Optional[dict] = None, share_qty: float = 0.0):
        self.ch, self.cfg, self.sym, self.scale = ch, cfg, sym, equity_scale
        self.book_cfg, self.share_qty = book_cfg or {}, share_qty
        self.shares = 0.0          # adjusted units held after falling back to the stock
        self.legs: List[Leg] = []
        self.cash = 0.0            # cumulative, negative = paid out
        self.costs = 0.0
        self.rolls = 0
        self.untraded_fills = 0
        self.switched = False
        self.adjustments = 0
        self.units = 0.0           # share units the position carries, fixed at entry

    def _fill(self, d, leg: Leg, qty: float, mid: float, traded: bool) -> None:
        h = self.cfg.half_spread * (1.0 if traded else 3.0) * mid
        h = max(h, 0.05)
        buy = qty > 0
        px = mid + h if buy else max(mid - h, 0.05)
        turnover = px * abs(qty)
        ch = _charges(d, turnover, buy, self.scale)
        self.cash -= px * qty + ch
        self.costs += ch + h * abs(qty)
        self.untraded_fills += 0 if traded else 1
        leg.qty += qty
        leg.mark = mid

    def _pick(self, d, F_ref: Optional[float] = None) -> Optional[List[Tuple[np.datetime64, float, float]]]:
        """[(expiry, strike, sign)] at traded strikes, or None when the chain cannot carry the trade."""
        exps = [e for e in self.ch.expiries(self.sym, d) if (e - d).astype(int) >= self.cfg.min_dte]
        if not exps:
            return None
        e = exps[0]
        F = self.ch.forward(self.sym, d, e)
        if not np.isfinite(F):
            return None
        k1 = self.ch.traded_strike(self.sym, d, e, "C", F * (1 + self.cfg.moneyness), self.cfg.strike_tol * F)
        if k1 is None:
            return None
        out = [(e, k1, 1.0)]
        if self.cfg.instrument == "spread":
            k2 = self.ch.traded_strike(self.sym, d, e, "C", k1 * (1 + self.cfg.width), self.cfg.strike_tol * F)
            if k2 is None or k2 <= k1:
                return None
            out.append((e, k2, -1.0))
        return out

    def open(self, d, units: Optional[float], budget: Optional[float]) -> bool:
        picks = self._pick(d)
        if picks is None:
            return False
        quotes = [self.ch.price(self.sym, d, e, k, "C", 0.35) for e, k, _ in picks]
        if any(not np.isfinite(q[0]) or q[0] <= 0 for q in quotes):
            return False
        if units is None:
            net = sum(sg * q[0] for (_, _, sg), q in zip(picks, quotes, strict=True))
            if net <= 0:
                return False
            units = budget / net
        self.units = units
        for (e, k, sg), (mid, traded, iv) in zip(picks, quotes, strict=True):
            leg = Leg(e, k, 0.0, iv=iv)
            self._fill(d, leg, sg * units, mid, traded)
            self.legs.append(leg)
        return True

    def value(self, d, close_adj: float = float("nan")) -> float:
        v = self.cash + (self.shares * close_adj if self.shares else 0.0)
        for leg in self.legs:
            if leg.qty == 0:
                continue
            mid, _, iv = self.ch.price(self.sym, d, leg.expiry, leg.strike, "C", leg.iv)
            if np.isfinite(mid):
                leg.mark, leg.iv = mid, iv
            v += leg.qty * leg.mark
        return v

    def close(self, d, close_adj: float = float("nan")) -> None:
        for leg in self.legs:
            if leg.qty != 0:
                mid, traded, _ = self.ch.price(self.sym, d, leg.expiry, leg.strike, "C", leg.iv)
                if not np.isfinite(mid):
                    mid, traded = leg.mark, False
                self._fill(d, leg, -leg.qty, mid, traded)
        self.legs = [leg for leg in self.legs if leg.qty != 0]
        if self.shares:
            b = self.book_cfg
            self.cash += close_adj * (1 - b["slippage"]) * self.shares * (1 - b["sell_charges"])
            self.shares = 0.0

    def to_shares(self, close_adj: float) -> None:
        """No liquid strike to roll into: carry the rest of the trade in the stock, as the share book would."""
        b = self.book_cfg
        self.cash -= close_adj * (1 + b["slippage"]) * self.share_qty * (1 + b["buy_charges"])
        self.shares = self.share_qty
        self.switched = True

    def adjust(self, a: float) -> None:
        """
        A bonus or split: NSE rescales open contracts so their value is unchanged.

        Strike is multiplied by `a` (the raw price's move relative to the true,
        adjusted one) and quantity divided by it. Without this a 2:1 bonus reads
        as the underlying halving under an unchanged strike.
        """
        for leg in self.legs:
            leg.strike *= a
            leg.qty /= a
            leg.mark *= a
        self.units /= a
        self.adjustments += 1

    def maybe_roll(self, d, close_adj: float) -> None:
        """Roll near expiry; when no liquid strike can take the roll, fall back to the stock."""
        if self.shares or not self.legs or (self.legs[0].expiry - d).astype(int) > self.cfg.roll_dte:
            return
        self.close(d)
        self.legs = []
        if self.open(d, self.units, None):
            self.rolls += 1
        elif np.isfinite(close_adj):
            self.to_shares(close_adj)


# ─────────────────────────────── the hybrid book ──────────────────────────────

def _share_path(tr: dict, cfg_book: dict, at_close: bool) -> np.ndarray:
    """
    Cumulative value of one share trade at each of its sessions' closes.

    `at_close=False` is the engine's own booking: filled at the entry session's
    open, sold at the exit session's open (or, for a stale exit, at the last
    price that session). Its final value equals the engine's `pnl` to the rupee.
    `at_close=True` is the timing control: the same trade at the options' closes.
    """
    q, c = tr["qty"], tr["closes"].copy()
    for i in range(1, len(c)):                      # the engine marks a missing close at the last price
        if not np.isfinite(c[i]):
            c[i] = c[i - 1]
    b, s, slip = cfg_book["buy_charges"], cfg_book["sell_charges"], cfg_book["slippage"]
    entry = c[0] * (1 + slip) if at_close else tr["entry"]
    exit_px = c[-1] * (1 - slip) if at_close else tr["exit"]
    cash = -entry * q * (1 + b)
    v = cash + q * c
    v[-1] = cash + exit_px * q * (1 - s)
    return v


def _action_factor(ch: Chains, sym: str, d0, d1, c0: float, c1: float) -> Optional[float]:
    """
    The raw future's move over the adjusted close's, when they part by more than 15%.

    Adjusted closes carry the corporate action and the raw future does not, so
    on an ex-date the two disagree by exactly the adjustment. Anything under 15%
    is left alone: no bonus or split is that small, and basis noise is far smaller.
    """
    common = sorted(set(ch.fut.get((sym, d0), {})) & set(ch.fut.get((sym, d1), {})))
    if not common or not (np.isfinite(c0) and np.isfinite(c1)) or c0 <= 0:
        return None
    e = common[0]
    f0, f1 = ch.fut[(sym, d0)][e], ch.fut[(sym, d1)][e]
    if f0 <= 0:
        return None
    a = (f1 / f0) / (c1 / c0)
    return a if abs(math.log(a)) > math.log(1.15) else None


def hybrid(book: dict, ch: Optional[Chains], cfg: Config, detail: bool = False) -> dict:
    """Daily factors of the share book with the eligible trades carried by `cfg`'s instrument."""
    dates, eq, twr = book["dates"], book["equity"], book["twr"]
    cb = book["cfg"]
    all_dates = book["all_dates"]
    off = int(np.searchsorted(all_dates, dates[0]))
    n = len(dates)
    delta = np.zeros(n)                              # rupees added to each session's P&L
    cash_gap = np.zeros(n)                           # cash the option frees, start of session
    daily_yield = (1 + cb["cash_yield"]) ** (1 / 252) - 1
    log = []
    for tr in book["trades"]:
        if not tr["fno"] or tr["entry_date"] < np.datetime64(OPT_FROM):
            continue
        i0, _i1 = tr["t0"] - off, tr["t1"] - off
        if i0 < 1:
            continue
        share = _share_path(tr, cb, at_close=False)
        scale = 1.0                                  # the book is in real rupees: Rs 20 is Rs 20
        sessions = all_dates[tr["t0"]:tr["t1"] + 1]
        share_cash = tr["entry"] * tr["qty"]
        if cfg.instrument == "shares_close":
            alt = _share_path(tr, cb, at_close=True)
            tied = np.full(len(alt), share_cash)
            meta: dict = {}
        elif cfg.instrument == "skip":
            # Control: the trade is not taken at all and its cash sits idle.
            alt = np.zeros(len(sessions))
            tied = np.zeros(len(sessions))
            meta = {}
        else:
            ot = OptionTrade(ch, cfg, tr["symbol"], scale, cb, tr["qty"])
            d0 = sessions[0]
            risk = tr["qty"] * (tr["entry"] - tr["init_stop"])
            exps = ch.expiries(tr["symbol"], d0)
            if not exps:
                ok = False
            elif cfg.sizing == "units":
                # The same rupee exposure as the shares, counted in today's unadjusted units.
                ok = ot.open(d0, cfg.size * share_cash / ch.forward(tr["symbol"], d0, exps[0]), None)
            else:
                ok = ot.open(d0, None, min(cfg.size * risk, share_cash))
            if not ok:
                log.append({"symbol": tr["symbol"], "entry_date": tr["entry_date"], "substituted": False})
                continue
            first = ot.legs[0]
            units0, strike0, expiry0 = first.qty, first.strike, first.expiry
            premium = -ot.cash
            alt = np.empty(len(sessions))
            tied = np.empty(len(sessions))
            closes = tr["closes"].copy()
            for k in range(1, len(closes)):             # the engine marks a missing close at the last price
                if not np.isfinite(closes[k]):
                    closes[k] = closes[k - 1]
            for k, d in enumerate(sessions):
                if k > 0:
                    a = _action_factor(ch, tr["symbol"], sessions[k - 1], d, closes[k - 1], closes[k])
                    if a is not None:
                        ot.adjust(a)
                if k == len(sessions) - 1:
                    ot.close(d, closes[k])
                elif k > 0:
                    ot.maybe_roll(d, closes[k])
                alt[k] = ot.value(d, closes[k])
                tied[k] = max(-ot.cash, 0.0)
            meta = {"premium": premium, "rolls": ot.rolls, "costs": ot.costs, "untraded": ot.untraded_fills,
                    "switched": ot.switched, "adjustments": ot.adjustments,
                    "opt_pnl": alt[-1], "share_pnl": share[-1], "risk": risk, "notional": share_cash,
                    "units": units0, "lot": ch.lot.get((tr["symbol"], expiry0)),
                    "strike": strike0, "expiry": expiry0}
        prev_s, prev_a = 0.0, 0.0
        for k in range(len(sessions)):
            i = i0 + k
            if i >= n:
                break
            delta[i] += (alt[k] - prev_a) - (share[k] - prev_s)
            prev_s, prev_a = share[k], alt[k]
            if k > 0:
                cash_gap[i] += share_cash - tied[k - 1]
        log.append(dict(symbol=tr["symbol"], entry_date=tr["entry_date"], substituted=True,
                        bars=tr["bars"], reason=tr["reason"], **meta))
    # Each session's change, as a fraction of the share book's opening equity,
    # then chain-linked: the hybrid compounds on its own equity from here.
    prev_eq = np.empty(n)
    prev_eq[0] = eq[0] / twr[0]
    prev_eq[1:] = eq[:-1]
    f = twr + (delta + cash_gap * daily_yield) / prev_eq
    out = {"cfg": cfg, "dates": dates, "factors": f, "substituted": sum(1 for r in log if r["substituted"]),
           "eligible": len(log)}
    if detail:
        out["log"] = log
    return out


# ─────────────────────────────── evaluation ───────────────────────────────────

def period(dates: np.ndarray, f: np.ndarray, start: str, end: Optional[str]) -> dict:
    lo = int(np.searchsorted(dates, np.datetime64(start)))
    hi = int(np.searchsorted(dates, np.datetime64(end))) if end else len(dates)
    x = f[lo:hi]
    curve = np.cumprod(x)
    years = (dates[hi - 1] - dates[lo]).astype(float) / 365.25
    cagr = float(curve[-1] ** (1 / years) - 1)
    dd = float((curve / np.maximum.accumulate(np.maximum(curve, 1.0)) - 1).min())
    return {"cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd < 0 else float("nan")}


def score(dates, f) -> dict:
    out = {"cal": period(dates, f, *CAL), "test": period(dates, f, *TEST)}
    out["subs"] = [period(dates, f, a, b)["cagr"] for a, b in SUBPERIODS]
    return out


def grid() -> List[Config]:
    cfgs = []
    for pre in PRESETS:
        cfgs.append(Config(preset=pre, instrument="shares_close"))
        cfgs.append(Config(preset=pre, instrument="skip"))
        for inst, m, dte, (mode, size) in itertools.product(
                ("call", "spread"), (-0.10, -0.05, 0.0, 0.05), (20, 45),
                (("units", 1.0), ("units", 1.5), ("risk", 0.5), ("risk", 1.0), ("risk", 2.0))):
            cfgs.append(Config(preset=pre, instrument=inst, moneyness=m, min_dte=dte, sizing=mode, size=size))
    return cfgs


def _load(mirror: str) -> Tuple[Dict[str, dict], Chains]:
    books = {}
    for pre in PRESETS:
        with open(os.path.join(mirror, f"book_{pre}.pkl"), "rb") as fh:
            books[pre] = pickle.load(fh)
    cache = os.path.join(mirror, "chains.pkl")
    if os.path.exists(cache):
        with open(cache, "rb") as fh:
            return books, pickle.load(fh)
    needs: Dict[str, List[Tuple[np.datetime64, np.datetime64]]] = {}
    for b in books.values():
        for tr in b["trades"]:
            if tr["fno"] and tr["entry_date"] >= np.datetime64(OPT_FROM):
                needs.setdefault(tr["symbol"], []).append((tr["entry_date"], tr["exit_date"]))
    ch = Chains(mirror, needs)
    with open(cache, "wb") as fh:
        pickle.dump(ch, fh, protocol=5)
    return books, ch


def smoke(mirror: str) -> None:
    books, ch = _load(mirror)
    for pre, b in books.items():
        worst = max(abs(_share_path(tr, b["cfg"], False)[-1] - tr["pnl"]) for tr in b["trades"])
        print(f"[smoke] {pre}: rebuilt share P&L vs engine, worst gap Rs {worst:.4f}")
        assert worst < 1.0, "share path does not reproduce the engine"
        none = hybrid(b, ch, Config(preset=pre, strike_tol=-1.0))
        gap = float(np.abs(none["factors"] - b["twr"]).max())
        print(f"[smoke] {pre}: nothing substituted -> factors equal the book's to {gap:.2e}")
        assert gap < 1e-12
        base = score(b["dates"], b["twr"])
        print(f"[smoke] {pre}: share book 2011-22 {base['cal']['cagr']:.2%} / {base['cal']['dd']:.2%}, "
              f"2024+ {base['test']['cagr']:.2%} / {base['test']['dd']:.2%}")
    ivs = np.concatenate([side.iv for q in ch.chain.values() for side in q.values()])
    print(f"[smoke] implied vol found for {np.isfinite(ivs).mean():.1%} of {len(ivs):,} traded option rows")
    assert np.isfinite(ivs).mean() > 0.8, "implied vols failing: check day counts"
    r = hybrid(books["deployed"], ch, Config(), detail=True)
    subs = [x for x in r["log"] if x["substituted"]]
    print(f"[smoke] deployed ATM call units x1: {len(subs)} of {r['eligible']} eligible substituted")
    lg = pd.DataFrame(subs)
    print(f"[smoke] option P&L vs share P&L, rank correlation across trades: "
          f"{lg.opt_pnl.rank().corr(lg.share_pnl.rank()):+.2f} (should be strongly positive)")
    for x in subs[:8]:
        print(f"   {x['symbol']:12s} {x['entry_date']!s} K={x['strike']:.1f} exp={x['expiry']} "
              f"prem={x['premium']:,.0f} notional={x['notional']:,.0f} rolls={x['rolls']} "
              f"opt={x['opt_pnl']:,.0f} shares={x['share_pnl']:,.0f}")


def _run_one(args) -> Tuple[Config, dict, int, int]:
    cfg, = args
    b = _BOOKS[cfg.preset]
    r = hybrid(b, _CH, cfg)
    return cfg, score(r["dates"], r["factors"]), r["substituted"], r["eligible"]


_BOOKS: Dict[str, dict] = {}
_CH: Optional[Chains] = None


def sweep(mirror: str) -> None:
    global _BOOKS, _CH
    _BOOKS, _CH = _load(mirror)
    from multiprocessing import get_context
    cfgs = grid()
    with get_context("fork").Pool(min(8, os.cpu_count() or 2)) as pool:
        rows = pool.map(_run_one, [(c,) for c in cfgs])
    base = {pre: score(b["dates"], b["twr"]) for pre, b in _BOOKS.items()}
    with open(os.path.join(mirror, "sweep.pkl"), "wb") as fh:
        pickle.dump({"rows": rows, "base": base}, fh)
    print(f"[sweep] {len(rows)} configs")


def _eligible(r: dict, base: dict, n_sub: int) -> bool:
    return n_sub >= MIN_SUBSTITUTED and all(a >= b for a, b in zip(r["subs"], base["subs"], strict=True))


def analyse(mirror: str) -> None:
    global _BOOKS, _CH
    with open(os.path.join(mirror, "sweep.pkl"), "rb") as fh:
        sw = pickle.load(fh)
    rows, base = sw["rows"], sw["base"]
    fmt = lambda p: f"{p['cagr']:7.2%} {p['dd']:8.2%} {p['calmar']:5.2f}"  # noqa: E731
    picks = {}
    print("\n== Selection on 2011-2022 only (test window not yet read) ==")
    for pre in PRESETS:
        mine = [r for r in rows if r[0].preset == pre and r[0].instrument not in ("shares_close", "skip")]
        ok = [r for r in mine if _eligible(r[1], base[pre], r[2])]
        print(f"{pre}: {len(ok)} of {len(mine)} configs pass (>= {MIN_SUBSTITUTED} substituted, "
              f"no sub-period below the share book)")
        pool = ok or mine
        pick = max(pool, key=lambda r: r[1]["cal"]["calmar"])
        picks[pre] = pick
        print(f"  pick: {pick[0].name}  cal {fmt(pick[1]['cal'])}  substituted {pick[2]}/{pick[3]}"
              + ("" if ok else "  (NO config passed; best Calmar shown, not recommended)"))
        top = sorted(mine, key=lambda r: -r[1]["cal"]["calmar"])[:8]
        for r in top:
            print(f"    {r[0].name:52s} cal {fmt(r[1]['cal'])}  subs " +
                  " ".join(f"{x:6.2%}" for x in r[1]["subs"]) + f"  n={r[2]}")
    print("\n== Test window, 2024 to date ==")
    print(f"{'book':52s} {'2011-22 CAGR':>12s} {'DD':>8s} {'Cal':>5s} | {'2024+ CAGR':>10s} {'DD':>8s} {'Cal':>5s}")
    for pre in PRESETS:
        ctrl = next(r for r in rows if r[0].preset == pre and r[0].instrument == "shares_close")
        skip = next(r for r in rows if r[0].preset == pre and r[0].instrument == "skip")
        for lab, sc in ((f"{pre}/shares (as traded)", base[pre]), (ctrl[0].name, ctrl[1]),
                        (skip[0].name, skip[1]), (picks[pre][0].name, picks[pre][1])):
            print(f"{lab:52s} {fmt(sc['cal'])} | {fmt(sc['test'])}")
        mine = [r for r in rows if r[0].preset == pre and r[0].instrument not in ("shares_close", "skip")]
        cal = np.array([r[1]["cal"]["cagr"] for r in mine])
        tst = np.array([r[1]["test"]["cagr"] for r in mine])
        rc = pd.Series(cal).rank().corr(pd.Series(tst).rank())
        beat = float(np.mean(tst > base[pre]["test"]["cagr"]))
        print(f"  grid: {len(mine)} configs; rank corr cal vs test CAGR {rc:+.2f}; "
              f"{beat:.0%} beat the share book in the test window; "
              f"test CAGR median {np.median(tst):.2%}, range {tst.min():.2%} .. {tst.max():.2%}")
    _BOOKS, _CH = _load(mirror)
    print("\n== The picks, in detail ==")
    for pre, pick in picks.items():
        cfg = pick[0]
        for hs in (cfg.half_spread, cfg.half_spread * 2, cfg.half_spread * 3):
            c2 = Config(**{**asdict(cfg), "half_spread": hs})
            r = hybrid(_BOOKS[pre], _CH, c2)
            sc = score(r["dates"], r["factors"])
            print(f"  {c2.name:60s} cal {fmt(sc['cal'])} | test {fmt(sc['test'])}")
        r = hybrid(_BOOKS[pre], _CH, cfg, detail=True)
        lg = pd.DataFrame([x for x in r["log"] if x["substituted"]])
        lg["yr"] = pd.to_datetime(lg.entry_date).dt.year
        lg["lots"] = lg.units / lg.lot
        lg["prem_pct"] = lg.premium / lg.notional
        for lab, g in (("2011-22", lg[lg.yr <= 2022]), ("2024+", lg[lg.yr >= 2024])):
            if g.empty:
                continue
            print(f"  {pre} {lab}: {len(g)} option trades, win {np.mean(g.opt_pnl > 0):.0%} "
                  f"(shares {np.mean(g.share_pnl > 0):.0%}); P&L options Rs {g.opt_pnl.sum()/1e5:,.1f}L "
                  f"vs shares Rs {g.share_pnl.sum()/1e5:,.1f}L; premium {g.prem_pct.median():.1%} of notional; "
                  f"rolls {g.rolls.mean():.2f}/trade; fell back to shares {g.switched.sum()}; "
                  f"corporate actions {g.adjustments.sum()}; lots median {g.lots.median():.1f} "
                  f"(< 1 lot: {np.mean(g.lots < 1):.0%})")
        miss = sum(1 for x in r["log"] if not x["substituted"])
        print(f"  {pre}: {miss} eligible trades had no traded strike near target and stayed in shares")


def main(argv: List[str]) -> None:
    cmd = argv[0] if argv else ""
    mirror = os.environ.get("FNO_MIRROR", "")
    if cmd == "extract":
        out = argv[argv.index("--out") + 1]
        yrs = argv[argv.index("--years") + 1] if "--years" in argv else "2008-2026"
        a, b = (int(x) for x in yrs.split("-"))
        extract(out, list(range(a, b + 1)))
    elif cmd == "trades":
        trades(mirror)
    elif cmd == "smoke":
        smoke(mirror)
    elif cmd == "sweep":
        sweep(mirror)
    elif cmd == "analyse":
        analyse(mirror)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    # Imported under its package name so pickled classes resolve from worker processes.
    from scripts import swing_options as _self
    _self.main(sys.argv[1:])
