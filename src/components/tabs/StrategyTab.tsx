import { useEffect, useState } from 'react';
import { T } from '../../theme';
import { Card, Label, Mono, Tag, ghostBtn, inkBtn, inputStyle } from '../ui';
import {
  fetchStrategy, addStrategyPosition, closeStrategyPosition, editStrategyPosition,
  resetStrategy,
  type StrategySummary, type StrategyPosition,
} from '../../lib/api';
import type { MarketData, Stock } from '../../lib/data';
import { useQueryParam } from '../../lib/router';
import type { Route } from '../../lib/router';

type Navigate = (target: string, opts?: { replace?: boolean }) => void;

const REFRESH_MS = 10 * 60_000; // the book only moves once a night

const lakh = (v: number) => '₹' + (v / 1e5).toFixed(2) + 'L';
const money = (v: number) =>
  Math.abs(v) >= 1e7 ? '₹' + (v / 1e7).toFixed(2) + 'cr' : lakh(v);
const pct = (v: number) => (v * 100).toFixed(2) + '%';
const rupees = (v: number) => '₹' + Math.round(v).toLocaleString('en-IN');

const th: React.CSSProperties = {
  textAlign: 'right', padding: '7px 10px', fontSize: 10.5, letterSpacing: '.08em',
  textTransform: 'uppercase', color: T.muted, fontWeight: 600,
  borderBottom: `1px solid ${T.border}`, whiteSpace: 'nowrap',
};
const td: React.CSSProperties = {
  textAlign: 'right', padding: '8px 10px', fontSize: 12.5,
  fontVariantNumeric: 'tabular-nums', borderBottom: `1px solid ${T.borderSoft}`,
  whiteSpace: 'nowrap', color: T.text,
};
const tdL = { ...td, textAlign: 'left' as const, color: T.ink, fontWeight: 600 };

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '22px 4px', color: T.muted, fontSize: 13 }}>{children}</div>;
}

export function StrategyTab({ D, route, navigate }: { D: MarketData; route: Route; navigate: Navigate }) {
  const [data, setData] = useState<StrategySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // In the URL, like the other tabs, so a book can be linked to directly.
  const [bookId, setBookId] = useQueryParam(route, navigate, 'book', 'balanced');
  const [nonce, setNonce] = useState(0);
  const [adding, setAdding] = useState(false);
  const [closing, setClosing] = useState<StrategyPosition | null>(null);
  const [editing, setEditing] = useState<StrategyPosition | null>(null);
  const [editingCapital, setEditingCapital] = useState(false);
  const reload = () => setNonce(n => n + 1);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchStrategy(bookId)
        .then(r => { if (!cancelled) { setData(r); setError(null); setLoading(false); } })
        .catch(e => {
          if (cancelled) return;
          setLoading(false);
          setError(e?.body?.message
            ?? 'Could not load the strategy book. Has the pipeline run yet?');
        });
    };
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [bookId, nonce]);

  if (loading) return <Empty>Loading the paper book…</Empty>;
  if (error) return <Card style={{ padding: '16px 20px' }}><Empty>{error}</Empty></Card>;
  if (!data) return null;

  const { state, performance: perf, signals, positions, closed, book } = data;
  const on = !!state?.regime_on;
  const editable = book.fillMode === 'manual';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* Regime first: when it is off, nothing else on this page matters. */}
      <Card style={{ padding: '16px 20px', borderLeft: `3px solid ${on ? T.up : T.down}` }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'baseline' }}>
          <div>
            <Label>Market regime</Label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
              <Mono size={22} color={on ? T.up : T.down} weight={600}>{on ? 'ON' : 'OFF'}</Mono>
              <Tag color={on ? T.up : T.down} bg={on ? T.upSoft : T.downSoft}>
                {on ? 'entries allowed' : 'flat — no entries'}
              </Tag>
            </div>
          </div>
          {state?.ew_index != null && state?.ew_ma != null && (
            <div>
              <Label>Index vs 100-day</Label>
              <Mono size={15}>
                {state.ew_index.toFixed(3)} vs {state.ew_ma.toFixed(3)}
                <span style={{ color: on ? T.up : T.down, marginLeft: 6 }}>
                  {((state.ew_index / state.ew_ma - 1) * 100).toFixed(2)}%
                </span>
              </Mono>
            </div>
          )}
          <div><Label>Session</Label><Mono size={15}>{state?.date ?? '—'}</Mono></div>
          <div><Label>Universe</Label><Mono size={15}>{state?.universe_n ?? '—'}</Mono></div>
          {data.books.length > 1 && (
            <div style={{ marginLeft: 'auto' }}>
              <Label>Book</Label>
              <div style={{ display: 'flex', gap: 6, marginTop: 3 }}>
                {data.books.map(b => (
                  <button key={b} onClick={() => setBookId(b)}
                    style={{ ...(b === bookId ? inkBtn : ghostBtn), padding: '5px 11px', fontSize: 12 }}>
                    {b === 'manual' ? 'Mine' : 'Rules'}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* Book */}
      <Card style={{ padding: '16px 20px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: 16 }}>
          <div><Label>Equity</Label><Mono size={19}>{money(state?.equity ?? 0)}</Mono></div>
          <div><Label>Cash</Label><Mono size={19}>{money(state?.cash ?? 0)}</Mono></div>
          <div><Label>Deployed</Label><Mono size={19}>{pct(state?.deployed ?? 0)}</Mono></div>
          <div>
            <Label>Positions</Label>
            <Mono size={19}>{state?.n_open ?? 0}
              <span style={{ color: T.faint, fontSize: 13 }}>
                {' / ' + String(book.config?.max_positions ?? '')}
              </span>
            </Mono>
          </div>
          <div>
            <Label>Total return</Label>
            <Mono size={19} color={(perf.totalReturn ?? 0) >= 0 ? T.up : T.down}>
              {perf.totalReturn == null ? '—' : pct(perf.totalReturn)}
            </Mono>
            {/* Said out loud because the deployed figure sits two cells away:
                this is on the whole book, so a third deployed and up 6% reads
                as 2% here, not 6%. */}
            <div style={{ fontSize: 10.5, color: T.faint }}>on equity, incl. cash</div>
          </div>
          <div>
            <Label>CAGR</Label>
            {/* Withheld under six months: annualising a few weeks is noise. */}
            <Mono size={19}>{perf.cagr == null ? '—' : pct(perf.cagr)}</Mono>
            {perf.cagr == null && (
              <div style={{ fontSize: 10.5, color: T.faint }}>after 6 months</div>
            )}
          </div>
        </div>
        <div style={{ marginTop: 10, fontSize: 11.5, color: T.faint, display: 'flex',
                      gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span>
            Book <b>{book.id}</b> · config v{book.configVersion} · {book.fillMode} fills ·
            {' '}base {money(book.capital)} · started {book.startedOn ?? '—'} ·
            {' '}{perf.days} session{perf.days === 1 ? '' : 's'}
          </span>
          <button style={{ ...ghostBtn, padding: '3px 9px', fontSize: 11 }}
            onClick={() => setEditingCapital(true)}>Change capital</button>
        </div>
      </Card>

      {/* Tomorrow's orders */}
      <Card style={{ padding: '16px 20px' }}>
        <Label>Buy at tomorrow&rsquo;s open</Label>
        <div style={{ fontSize: 11.5, color: T.faint, margin: '2px 0 8px' }}>
          Signals come from today&rsquo;s close and are filled at the next open — that is what was
          backtested. Acting intraday on them is untested.
        </div>
        {signals.length === 0 ? (
          <Empty>{on
            ? 'No qualifying breakouts from this session.'
            : 'Regime is off — the strategy is not entering.'}</Empty>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 720 }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>#</th>
                <th style={{ ...th, textAlign: 'left' }}>Symbol</th>
                <th style={{ ...th, textAlign: 'left' }}>Sector</th>
                <th style={th}>Ref close</th><th style={th}>Stop</th>
                <th style={th}>Stop away</th><th style={th}>Qty</th>
                <th style={th}>Position</th><th style={th}>At risk</th>
              </tr></thead>
              <tbody>
                {signals.map(s => (
                  <tr key={s.symbol}>
                    <td style={{ ...td, textAlign: 'left', color: T.faint }}>{s.rank}</td>
                    <td style={tdL}>{s.symbol}</td>
                    <td style={{ ...td, textAlign: 'left', color: T.muted, fontSize: 11.5 }}>
                      {s.sector ?? '—'}
                    </td>
                    <td style={td}>{s.ref_close.toFixed(2)}</td>
                    <td style={{ ...td, color: T.down }}>{s.stop.toFixed(2)}</td>
                    {/* Shown so the position size is checkable: qty follows
                        from risk / stop distance, not from equal weighting. */}
                    <td style={td}>{(s.stop_pct * 100).toFixed(1)}%</td>
                    <td style={td}>{s.qty}</td>
                    <td style={td}>{lakh(s.position_value)}</td>
                    <td style={{ ...td, color: T.amber }}>{rupees(s.risk_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Open positions */}
      <Card style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
          <Label>Open positions</Label>
          {editable && (
            <button style={{ ...ghostBtn, padding: '5px 11px', fontSize: 12 }}
              onClick={() => setAdding(a => !a)}>{adding ? 'Cancel' : '+ Add position'}</button>
          )}
        </div>
        {editable && adding && (
          <AddPosition book={book.id} config={book.config} equity={state?.equity ?? 0}
            session={state?.date ?? ''} signals={signals} stocks={D.stocks}
            onDone={() => { setAdding(false); reload(); }} />
        )}
        {positions.length === 0 ? (
          <Empty>The book is flat.</Empty>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 760 }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>Symbol</th>
                <th style={{ ...th, textAlign: 'left' }}>Entered</th>
                <th style={th}>Entry</th><th style={th}>Last</th><th style={th}>Stop</th>
                <th style={th}>P&amp;L</th><th style={th}>R</th>
                <th style={th}>Held</th>
                <th style={{ ...th, textAlign: 'left' }}>Action</th>
              </tr></thead>
              <tbody>
                {positions.map(p => {
                  const last = p.last_px ?? p.entry_px;
                  const pnl = (last - p.entry_px) * p.qty;
                  const r = p.r_per_share > 0 ? (last - p.entry_px) / p.r_per_share : 0;
                  const maxBars = Number(book.config?.time_stop ?? 60);
                  return (
                    <tr key={p.id}>
                      <td style={tdL}>{p.symbol}</td>
                      <td style={{ ...td, textAlign: 'left', color: T.muted }}>{p.entry_date}</td>
                      <td style={td}>{p.entry_px.toFixed(2)}</td>
                      <td style={td}>{last.toFixed(2)}</td>
                      <td style={{ ...td, color: T.down }}>{p.stop.toFixed(2)}</td>
                      <td style={{ ...td, color: pnl >= 0 ? T.up : T.down }}>{rupees(pnl)}</td>
                      <td style={{ ...td, color: r >= 0 ? T.up : T.down }}>{r.toFixed(2)}R</td>
                      <td style={td}>
                        {p.bars}
                        <span style={{ color: T.faint }}>{' / ' + maxBars}</span>
                      </td>
                      <td style={{ ...td, textAlign: 'left' }}>
                        {/* On a manual book the badge is advice, not an
                            instruction — the engine never closes these. */}
                        {p.pending_exit
                          ? <Tag color={T.down} bg={T.downSoft}>
                              {editable ? 'rules say sell' : 'sell at open'} · {p.pending_exit}
                            </Tag>
                          : <Tag color={T.up} bg={T.upSoft}>hold</Tag>}
                        {editable && (
                          <>
                            <button style={{ ...ghostBtn, padding: '3px 9px', fontSize: 11, marginLeft: 8 }}
                              onClick={() => setEditing(p)}>Edit</button>
                            <button style={{ ...ghostBtn, padding: '3px 9px', fontSize: 11, marginLeft: 6 }}
                              onClick={() => setClosing(p)}>Close</button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Closed trades */}
      {closed.length > 0 && (
        <Card style={{ padding: '16px 20px' }}>
          <Label>Closed trades</Label>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 700 }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>Symbol</th>
                <th style={{ ...th, textAlign: 'left' }}>In</th>
                <th style={{ ...th, textAlign: 'left' }}>Out</th>
                <th style={th}>Held</th><th style={th}>P&amp;L</th><th style={th}>R</th>
                <th style={{ ...th, textAlign: 'left' }}>Reason</th>
              </tr></thead>
              <tbody>
                {closed.map((c, i) => (
                  <tr key={`${c.symbol}-${c.exit_date}-${i}`}>
                    <td style={tdL}>{c.symbol}</td>
                    <td style={{ ...td, textAlign: 'left', color: T.muted }}>{c.entry_date}</td>
                    <td style={{ ...td, textAlign: 'left', color: T.muted }}>{c.exit_date}</td>
                    <td style={td}>{c.bars}</td>
                    <td style={{ ...td, color: c.pnl >= 0 ? T.up : T.down }}>{rupees(c.pnl)}</td>
                    <td style={{ ...td, color: c.r_multiple >= 0 ? T.up : T.down }}>
                      {c.r_multiple.toFixed(2)}R
                    </td>
                    <td style={{ ...td, textAlign: 'left', color: T.muted }}>{c.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {editing && (
        <EditPosition pos={editing}
          onClose={() => setEditing(null)}
          onDone={() => { setEditing(null); reload(); }} />
      )}

      {closing && (
        <ClosePosition pos={closing} session={state?.date ?? ''}
          onDone={() => { setClosing(null); reload(); }} />
      )}

      {editingCapital && (
        <ChangeCapital current={book.capital}
          onClose={() => setEditingCapital(false)}
          onDone={() => { setEditingCapital(false); reload(); }} />
      )}
    </div>
  );
}

/*
 * Add a position to the manual book.
 *
 * Quantity is suggested from the book's own config — risk % of equity over the
 * stop distance, then the weight cap — but stays editable, because the point of
 * this book is to record what you actually did, not what the sizer preferred.
 * The suggestion is computed here rather than server-side so there is no second
 * copy of the sizing rules to drift from pipeline/compute/strategy/rules.py.
 *
 * The last price is part of the entry for the same reason: a hand-added trade
 * is usually already running, and marking it at its entry until the next
 * nightly run would report a flat P&L on a position that is not flat.
 */
function AddPosition({ book, config, equity, session, signals, stocks, onDone }: {
  book: string;
  config: Record<string, unknown> | null;
  equity: number;
  session: string;
  signals: StrategySummary['signals'];
  stocks: Stock[];
  onDone: () => void;
}) {
  const [sym, setSym] = useState('');
  const [entry, setEntry] = useState('');
  const [stop, setStop] = useState('');
  const [qty, setQty] = useState('');
  const [last, setLast] = useState('');
  const [date, setDate] = useState(session);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const riskPct = Number(config?.risk_pct ?? 0.006);
  const maxWeight = Number(config?.max_weight ?? 0.1);
  const e = Number(entry);
  const st = Number(stop);
  const suggested = e > 0 && st > 0 && st < e
    ? Math.max(0, Math.floor(Math.min(riskPct * equity / (e - st), maxWeight * equity / e)))
    : 0;
  const atRisk = Number(qty) > 0 && e > st ? Number(qty) * (e - st) : 0;

  /*
   * The trade as it stands right now, not as it was entered. A position taken
   * a fortnight ago is already up or down, and typing the mark in here is what
   * makes the P&L and R in the table below true the moment it is added, rather
   * than after the next nightly run.
   */
  const lx = Number(last) > 0 ? Number(last) : e;
  const q = Math.floor(Number(qty));
  const openPnl = q > 0 && e > 0 ? (lx - e) * q : 0;
  const rNow = e > st && st > 0 ? (lx - e) / (e - st) : 0;

  // Picking a name the engine flagged fills the row from its own figures.
  const prefill = (s: StrategySummary['signals'][number]) => {
    setSym(s.symbol); setEntry(String(s.ref_close));
    setStop(String(s.stop)); setQty(String(s.qty)); setLast(String(s.ref_close));
  };

  const picked = stocks.find(x => x.sym === sym.trim().toUpperCase());

  const submit = () => {
    setBusy(true); setErr(null);
    addStrategyPosition({
      book, symbol: sym.trim().toUpperCase(), entry_date: date,
      entry_px: e, stop: st, qty: q, last_px: lx,
    })
      .then(onDone)
      .catch(x => { setBusy(false); setErr(x?.body?.message ?? 'Could not add the position.'); });
  };

  const field = { ...inputStyle, width: '100%', fontSize: 13 };
  const cell = { display: 'flex', flexDirection: 'column' as const, gap: 4 };

  return (
    <div style={{ margin: '12px 0 4px', padding: 14, background: T.cardAlt,
                  borderRadius: 8, border: `1px solid ${T.border}` }}>
      {signals.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <Label>Prefill from tonight&rsquo;s signals</Label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 5 }}>
            {signals.map(s => (
              <button key={s.symbol} onClick={() => prefill(s)}
                style={{ ...ghostBtn, padding: '4px 10px', fontSize: 11.5 }}>{s.symbol}</button>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(110px,1fr))', gap: 10 }}>
        <div style={cell}><Label>Symbol</Label>
          {/* Picked from the session's own universe, so the entry price starts
              at a real close rather than something typed from memory. */}
          <input style={field} value={sym} list="pulse-symbols" placeholder="Search…"
            onChange={ev => {
              const v = ev.target.value.toUpperCase();
              setSym(v);
              const hit = stocks.find(x => x.sym === v);
              if (hit && !entry) setEntry(String(hit.price));
              if (hit && !last) setLast(String(hit.price));
            }} />
          <datalist id="pulse-symbols">
            {stocks.map(x => <option key={x.sym} value={x.sym}>{x.sector}</option>)}
          </datalist>
        </div>
        <div style={cell}><Label>Entry date</Label>
          {/* A real picker: a manual entry is usually backdated, and typing
              ISO dates from memory is where the wrong ones come from. */}
          <input style={field} type="date" value={date}
            onChange={ev => setDate(ev.target.value)} /></div>
        <div style={cell}><Label>Entry price</Label>
          <input style={field} value={entry} onChange={ev => setEntry(ev.target.value)} inputMode="decimal" /></div>
        <div style={cell}><Label>Stop</Label>
          <input style={field} value={stop} onChange={ev => setStop(ev.target.value)} inputMode="decimal" /></div>
        <div style={cell}><Label>Quantity</Label>
          <input style={field} value={qty} onChange={ev => setQty(ev.target.value)} inputMode="numeric" /></div>
        <div style={cell}><Label>Last price</Label>
          <input style={field} value={last} onChange={ev => setLast(ev.target.value)}
            inputMode="decimal" placeholder={entry || 'mark'} /></div>
      </div>
      {picked && (
        <div style={{ marginTop: 8, fontSize: 12, color: T.muted }}>
          {picked.sector} · last close <b style={{ color: T.ink }}>{picked.price.toFixed(2)}</b>
          {' '}· {picked.chg1d >= 0 ? '+' : ''}{picked.chg1d.toFixed(2)}% today
          <button style={{ ...ghostBtn, padding: '2px 8px', fontSize: 11, marginLeft: 8 }}
            onClick={() => setEntry(String(picked.price))}>Use as entry</button>
          <button style={{ ...ghostBtn, padding: '2px 8px', fontSize: 11, marginLeft: 6 }}
            onClick={() => setLast(String(picked.price))}>Use as last</button>
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 11, flexWrap: 'wrap' }}>
        {suggested > 0 && (
          <button style={{ ...ghostBtn, padding: '5px 11px', fontSize: 12 }}
            onClick={() => setQty(String(suggested))}>
            Suggest {suggested} ({(riskPct * 100).toFixed(2)}% risk)
          </button>
        )}
        {atRisk > 0 && (
          <span style={{ fontSize: 12.5, color: T.muted }}>
            At risk <b style={{ color: T.amber }}>{rupees(atRisk)}</b>
            {equity > 0 && ` · ${((atRisk / equity) * 100).toFixed(2)}% of book`}
          </span>
        )}
        {q > 0 && e > 0 && lx !== e && (
          <span style={{ fontSize: 12.5, color: T.muted }}>
            Open P&amp;L <b style={{ color: openPnl >= 0 ? T.up : T.down }}>{rupees(openPnl)}</b>
            <span style={{ color: T.faint }}> · {rNow.toFixed(2)}R</span>
          </span>
        )}
        <button style={{ ...inkBtn, padding: '6px 14px', fontSize: 12.5, marginLeft: 'auto' }}
          disabled={busy} onClick={submit}>{busy ? 'Adding…' : 'Add to my book'}</button>
      </div>
      {err && <div style={{ marginTop: 8, fontSize: 12.5, color: T.down }}>{err}</div>}
    </div>
  );
}

/*
 * Correct an open position on the manual book.
 *
 * The rules book is a test and is never touched by hand; this one is a record
 * of what the operator actually did, so it has to be correctable — a mistyped
 * fill, a stop moved up during the session, a mark newer than last night's
 * close. Everything derived from a position (P&L, R, held) comes from these
 * five fields, so they are edited together and previewed here before saving.
 */
function EditPosition({ pos, onClose, onDone }: {
  pos: StrategyPosition; onClose: () => void; onDone: () => void;
}) {
  const [entry, setEntry] = useState(String(pos.entry_px));
  const [stop, setStop] = useState(String(pos.stop));
  const [last, setLast] = useState(String(pos.last_px ?? pos.entry_px));
  const [qty, setQty] = useState(String(pos.qty));
  const [date, setDate] = useState(pos.entry_date);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const e = Number(entry);
  const st = Number(stop);
  const lx = Number(last);
  const q = Math.floor(Number(qty));

  /*
   * Mirrors the server's rule so the preview cannot disagree with what is
   * saved: stops only ratchet up, so a higher stop is a trail and R stays
   * measured from the stop the position was sized against; a lower one cannot
   * be a trail, so it is a correction and the baseline moves with it.
   */
  const resets = st < pos.init_stop;
  const baseline = resets ? st : pos.init_stop;
  const rPerShare = e - baseline;
  const pnl = q > 0 ? (lx - e) * q : 0;
  const r = rPerShare > 0 ? (lx - e) / rPerShare : 0;
  const valid = e > 0 && st > 0 && st < e && lx > 0 && q > 0 && rPerShare > 0
    && /^\d{4}-\d{2}-\d{2}$/.test(date);

  const submit = () => {
    setBusy(true); setErr(null);
    editStrategyPosition(pos.id, {
      entry_date: date, entry_px: e, stop: st, qty: q, last_px: lx,
    })
      .then(onDone)
      .catch(x => { setBusy(false); setErr(x?.body?.message ?? 'Could not save the position.'); });
  };

  const field = { ...inputStyle, width: '100%', fontSize: 13 };
  const cell = { display: 'flex', flexDirection: 'column' as const, gap: 4 };

  return (
    <div role="dialog" aria-label={`Edit ${pos.symbol}`}
      style={{ position: 'fixed', inset: 0, background: 'rgba(11,18,32,.35)', display: 'flex',
               alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}
      onClick={onClose}>
      <div onClick={ev => ev.stopPropagation()}
        style={{ background: T.card, borderRadius: 12, padding: 20, width: 460,
                 maxWidth: '100%', boxShadow: T.shadowPop }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: T.ink }}>Edit {pos.symbol}</div>
        <div style={{ fontSize: 12.5, color: T.muted, margin: '4px 0 14px' }}>
          Held {pos.bars} session{pos.bars === 1 ? '' : 's'} · entered {pos.entry_date}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
          <div style={cell}><Label>Entry date</Label>
            <input style={field} type="date" value={date}
              onChange={ev => setDate(ev.target.value)} /></div>
          <div style={cell}><Label>Entry price</Label>
            <input style={field} value={entry} inputMode="decimal"
              onChange={ev => setEntry(ev.target.value)} /></div>
          <div style={cell}><Label>Quantity</Label>
            <input style={field} value={qty} inputMode="numeric"
              onChange={ev => setQty(ev.target.value)} /></div>
          <div style={cell}><Label>Stop</Label>
            <input style={field} value={stop} inputMode="decimal"
              onChange={ev => setStop(ev.target.value)} /></div>
          <div style={cell}><Label>Last price</Label>
            <input style={field} value={last} inputMode="decimal"
              onChange={ev => setLast(ev.target.value)} /></div>
        </div>

        <div style={{ margin: '13px 0 4px', fontSize: 13 }}>
          P&amp;L <b style={{ color: pnl >= 0 ? T.up : T.down }}>{rupees(pnl)}</b>
          <span style={{ color: T.faint }}>
            {' · '}{r.toFixed(2)}R{rPerShare > 0 && ` · risk ${rupees(rPerShare * q)}`}
          </span>
        </div>
        <div style={{ fontSize: 11.5, color: T.faint, lineHeight: 1.5 }}>
          {resets
            ? `R is measured from ${st.toFixed(2)} — lowering the stop resets the baseline.`
            : `R is measured from the initial stop ${pos.init_stop.toFixed(2)}, so a trailing `
              + 'stop does not rebase past trades.'}
          {date !== pos.entry_date
            ? ' Sessions held are recounted from the new entry date.'
            : ''}
          {' '}The next nightly run overwrites the last price with the real close.
        </div>

        {err && <div style={{ fontSize: 12.5, color: T.down, marginTop: 10 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
          <button style={{ ...ghostBtn, padding: '6px 13px', fontSize: 12.5 }} onClick={onClose}>Cancel</button>
          <button style={{ ...inkBtn, padding: '6px 14px', fontSize: 12.5,
                           opacity: valid && !busy ? 1 : 0.5 }}
            disabled={!valid || busy} onClick={submit}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    </div>
  );
}

/* Close a manual position. Charges match the backtest so the P&L is comparable. */
function ClosePosition({ pos, session, onDone }: {
  pos: StrategyPosition; session: string; onDone: () => void;
}) {
  const [px, setPx] = useState(String(pos.last_px ?? pos.entry_px));
  const [date, setDate] = useState(session);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const gross = (Number(px) - pos.entry_px) * pos.qty;
  const r = pos.r_per_share > 0 ? (Number(px) - pos.entry_px) / pos.r_per_share : 0;

  const submit = () => {
    setBusy(true); setErr(null);
    closeStrategyPosition(pos.id, date, Number(px), 'manual')
      .then(onDone)
      .catch(x => { setBusy(false); setErr(x?.body?.message ?? 'Could not close the position.'); });
  };

  return (
    <div role="dialog" aria-label={`Close ${pos.symbol}`}
      style={{ position: 'fixed', inset: 0, background: 'rgba(11,18,32,.35)', display: 'flex',
               alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}
      onClick={onDone}>
      <div onClick={ev => ev.stopPropagation()}
        style={{ background: T.card, borderRadius: 12, padding: 20, width: 360,
                 maxWidth: '100%', boxShadow: T.shadowPop }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: T.ink }}>Close {pos.symbol}</div>
        <div style={{ fontSize: 12.5, color: T.muted, margin: '4px 0 14px' }}>
          {pos.qty} @ {pos.entry_px.toFixed(2)} · entered {pos.entry_date}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <div><Label>Exit price</Label>
            <input style={{ ...inputStyle, width: '100%', fontSize: 13 }} value={px}
              onChange={ev => setPx(ev.target.value)} inputMode="decimal" /></div>
          <div><Label>Exit date</Label>
            <input style={{ ...inputStyle, width: '100%', fontSize: 13 }} type="date" value={date}
              min={pos.entry_date} onChange={ev => setDate(ev.target.value)} /></div>
        </div>
        <div style={{ margin: '12px 0', fontSize: 13 }}>
          Gross <b style={{ color: gross >= 0 ? T.up : T.down }}>{rupees(gross)}</b>
          <span style={{ color: T.faint }}> · {r.toFixed(2)}R before charges</span>
        </div>
        {err && <div style={{ fontSize: 12.5, color: T.down, marginBottom: 8 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button style={{ ...ghostBtn, padding: '6px 13px', fontSize: 12.5 }} onClick={onDone}>Cancel</button>
          <button style={{ ...inkBtn, padding: '6px 14px', fontSize: 12.5 }}
            disabled={busy} onClick={submit}>{busy ? 'Closing…' : 'Close position'}</button>
        </div>
      </div>
    </div>
  );
}

/*
 * Change the opening capital, which means starting over.
 *
 * Every figure in a book — position sizes, drawdown, return, CAGR — was
 * computed against the capital it began with. Re-basing that number without
 * clearing the history would produce a record of something that never happened,
 * so the two are deliberately the same action, and the dialog says so plainly
 * rather than burying it behind a confirm.
 */
function ChangeCapital({ current, onClose, onDone }: {
  current: number; onClose: () => void; onDone: () => void;
}) {
  const [lakh, setLakh] = useState(String(Math.round(current / 1e5)));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const next = Number(lakh) * 1e5;
  const changed = Number.isFinite(next) && next > 0 && Math.round(next) !== Math.round(current);

  const submit = () => {
    setBusy(true); setErr(null);
    resetStrategy(next)
      .then(onDone)
      .catch(x => { setBusy(false); setErr(x?.body?.message ?? 'Could not reset the books.'); });
  };

  return (
    <div role="dialog" aria-label="Change opening capital"
      style={{ position: 'fixed', inset: 0, background: 'rgba(11,18,32,.35)', display: 'flex',
               alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}
      onClick={onClose}>
      <div onClick={ev => ev.stopPropagation()}
        style={{ background: T.card, borderRadius: 12, padding: 22, width: 400,
                 maxWidth: '100%', boxShadow: T.shadowPop }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: T.ink }}>Opening capital</div>
        <div style={{ fontSize: 12.5, color: T.muted, margin: '4px 0 14px' }}>
          Currently {money(current)}.
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14, color: T.muted }}>₹</span>
          <input style={{ ...inputStyle, width: 110, fontSize: 14 }} value={lakh}
            onChange={ev => setLakh(ev.target.value)} inputMode="numeric" />
          <span style={{ fontSize: 14, color: T.muted }}>lakh</span>
          {Number.isFinite(next) && next > 0 && (
            <span style={{ fontSize: 12.5, color: T.faint, marginLeft: 4 }}>= {money(next)}</span>
          )}
        </div>

        <div style={{ marginTop: 14, padding: '11px 13px', borderRadius: 8,
                      background: T.downSoft, border: `1px solid ${T.down}33` }}>
          <div style={{ fontSize: 12.5, color: T.down, fontWeight: 600, marginBottom: 3 }}>
            This clears both books
          </div>
          <div style={{ fontSize: 12, color: T.text, lineHeight: 1.5 }}>
            Every position, closed trade and session of history is deleted, on the rules
            book and yours. The next nightly run starts again from the new capital.
            There is no undo.
          </div>
        </div>

        {err && <div style={{ fontSize: 12.5, color: T.down, marginTop: 10 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button style={{ ...ghostBtn, padding: '6px 13px', fontSize: 12.5 }} onClick={onClose}>
            Cancel
          </button>
          <button style={{ ...inkBtn, padding: '6px 14px', fontSize: 12.5,
                           opacity: changed && !busy ? 1 : 0.5 }}
            disabled={!changed || busy} onClick={submit}>
            {busy ? 'Resetting…' : 'Reset and start over'}
          </button>
        </div>
      </div>
    </div>
  );
}
