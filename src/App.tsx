import { useEffect, useState } from 'react';
import { T, dirColor } from './theme';
import { areaLine } from './lib/svg';
import { useProfile } from './lib/profile';
import { useMarket } from './lib/useMarket';
import { logout } from './lib/api';
import { LoginGate, BootstrapScreen } from './components/LoginGate';
import { Mono, Sparkline, ghostBtn, inkBtn, inputStyle } from './components/ui';
import { TopBar } from './components/TopBar';
import { StockDrawer } from './components/StockDrawer';
import { BreadthTab } from './components/tabs/BreadthTab';
import { ChartsTab } from './components/tabs/ChartsTab';
import { SectorsTab } from './components/tabs/SectorsTab';
import { HighsTab } from './components/tabs/HighsTab';
import type { HighMode } from './components/tabs/HighsTab';
import { DrawdownTab } from './components/tabs/DrawdownTab';
import { WatchTab } from './components/tabs/WatchTab';

const DEFAULT_QUOTES = [
  'The trend is your friend until the end when it bends.',
  'Trade the setup, not the P&L.',
  'When breadth narrows, tighten stops — the market whispers before it shouts.',
  'Buy strength in strong sectors; never average down a swing trade.',
  'No setup, no trade. Cash is a position.',
];

type TabId = 'breadth' | 'chart' | 'sectors' | 'highs' | 'draw' | 'watch';
type CardMode = 'area' | 'bar';

function useWatchlist() {
  const [watch, setWatch] = useState<Record<string, true>>({});
  useEffect(() => {
    try {
      const w = localStorage.getItem('pulse-watchlist');
      if (w) setWatch(JSON.parse(w));
    } catch { /* ignore corrupt storage */ }
  }, []);
  const toggle = (sym: string) => {
    setWatch(prev => {
      const next = { ...prev };
      if (next[sym]) delete next[sym]; else next[sym] = true;
      try { localStorage.setItem('pulse-watchlist', JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  };
  return { watch, toggle };
}

function useQuotes() {
  const [quotes, setQuotes] = useState<string[]>(DEFAULT_QUOTES);
  const [quoteIdx, setQuoteIdx] = useState(0);
  useEffect(() => {
    try {
      const q = localStorage.getItem('pulse-quotes');
      const loaded = q ? JSON.parse(q) : DEFAULT_QUOTES;
      setQuotes(loaded);
      setQuoteIdx(Math.floor(Math.random() * loaded.length));
    } catch { /* ignore corrupt storage */ }
  }, []);
  const shuffle = () => {
    if (quotes.length < 2) return;
    let n = Math.floor(Math.random() * quotes.length);
    if (n === quoteIdx) n = (n + 1) % quotes.length;
    setQuoteIdx(n);
  };
  const addQuote = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const next = [...quotes, trimmed];
    setQuotes(next);
    setQuoteIdx(next.length - 1);
    try { localStorage.setItem('pulse-quotes', JSON.stringify(next)); } catch { /* ignore */ }
  };
  return { quoteText: quotes[quoteIdx % quotes.length], shuffle, addQuote };
}

export default function App() {
  const [tab, setTab] = useState<TabId>('breadth');
  const [chartSym, setChartSym] = useState('NIFTY 50');
  const [highMode, setHighMode] = useState<HighMode>('w52');
  const [sectorFilter, setSectorFilter] = useState('All sectors');
  const [cardMode, setCardMode] = useState<Record<string, CardMode>>({});
  const [addingQuote, setAddingQuote] = useState(false);
  const [quoteDraft, setQuoteDraft] = useState('');
  const [drawerSym, setDrawerSym] = useState<string | null>(null);

  const { watch, toggle } = useWatchlist();
  const { quoteText, shuffle, addQuote } = useQuotes();
  const { profile, update } = useProfile();
  const market = useMarket();

  if (market.auth === 'checking') {
    return <div style={{ minHeight: '100vh', background: T.bg }} />;
  }
  if (market.auth === 'anon') {
    return <LoginGate onDemo={market.enableDemo} />;
  }
  if (!market.data) {
    return <BootstrapScreen backfill={market.backfill ?? { running: true, done: 0, target: 0, currentDate: null, error: null }} />;
  }

  const D = market.data;
  const drawerStock = drawerSym ? D.stocks.find(s => s.sym === drawerSym) || null : null;

  const setCard = (id: string, m: CardMode) => setCardMode(prev => ({ ...prev, [id]: m }));

  const saveDraft = () => {
    addQuote(quoteDraft);
    setAddingQuote(false);
    setQuoteDraft('');
  };

  const tabs: { id: TabId; label: string }[] = [
    { id: 'breadth', label: 'Breadth' },
    { id: 'chart', label: 'Charts' },
    { id: 'sectors', label: 'Sectors' },
    { id: 'highs', label: 'Highs' },
    { id: 'draw', label: 'Drawdown' },
    { id: 'watch', label: 'Watchlist · ' + Object.keys(watch).length },
  ];

  const indices = D.indices.map(ix => {
    const p = areaLine(ix.pts, 100, 30, 3, 3);
    const isVix = ix.name === 'INDIA VIX';
    const color = isVix ? T.amber : dirColor(ix.chgPct);
    const fill = isVix ? T.amberSoft : ix.chgPct >= 0 ? T.upSoft : T.downSoft;
    return {
      name: ix.name,
      value: ix.value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      chg: (ix.chgPct >= 0 ? '+' : '') + ix.chgPct.toFixed(2) + '%',
      color, fill, line: p.line, area: p.area,
      open: () => { setTab('chart'); setChartSym(ix.name); },
    };
  });

  return (
    <div style={{ minHeight: '100vh', background: T.bg }}>
      <TopBar
        profile={profile} updateProfile={update} watchCount={Object.keys(watch).length}
        sessionUser={market.user} dataSource={market.source} asOf={market.asOf}
        onLogout={() => { void logout().then(() => window.location.reload()); }}
      />

      <div style={{ maxWidth: 1280, margin: '0 auto', padding: '20px 28px 72px' }}>
        {/* Index strip */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
          {indices.map(ix => (
            <div key={ix.name} onClick={ix.open} title="Open chart" style={{ minWidth: 0, cursor: 'pointer', background: T.card, border: '1px solid ' + T.border, borderRadius: T.radius, padding: '11px 13px', boxShadow: T.shadow }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
                <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.02em', color: T.muted, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{ix.name}</span>
                <Mono size={11} weight={600} color={ix.color} style={{ whiteSpace: 'nowrap' }}>{ix.chg}</Mono>
              </div>
              <Mono size={15} weight={600} style={{ display: 'block', marginTop: 4 }}>{ix.value}</Mono>
              <div style={{ marginTop: 7 }}>
                <Sparkline line={ix.line} area={ix.area} color={ix.color} fill={ix.fill} height={30} />
              </div>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginTop: 22, borderBottom: '1px solid ' + T.border }}>
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} style={{ appearance: 'none', border: 'none', cursor: 'pointer', fontFamily: T.sans, fontSize: 14, fontWeight: 600, padding: '11px 16px 13px', background: 'transparent', color: tab === t.id ? T.ink : T.faint, borderBottom: '2px solid ' + (tab === t.id ? T.amber : 'transparent'), marginBottom: -1 }}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Rule of the day */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16, padding: '10px 16px', background: T.card, border: '1px solid ' + T.border, borderRadius: 12, boxShadow: T.shadow }}>
          <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.1em', color: T.amber }}>RULE</span>
          <div style={{ fontFamily: T.serif, fontSize: 13.5, color: T.text, fontStyle: 'italic', flex: 1, minWidth: 0 }}>&ldquo;{quoteText}&rdquo;</div>
          <button onClick={shuffle} title="Next" style={{ ...ghostBtn, fontSize: 14, lineHeight: 1 }}>&#8635;</button>
          <button onClick={() => setAddingQuote(v => !v)} style={{ ...ghostBtn, fontSize: 12.5, fontWeight: 600 }}>{addingQuote ? 'Cancel' : '+ Add rule'}</button>
        </div>
        {addingQuote && (
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <input
              value={quoteDraft}
              onChange={e => setQuoteDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveDraft(); }}
              placeholder="Write a rule or reminder for yourself…"
              style={{ ...inputStyle, flex: 1 }}
            />
            <button onClick={saveDraft} style={inkBtn}>Save</button>
          </div>
        )}

        {tab === 'breadth' && <BreadthTab D={D} cardMode={cardMode} setCard={setCard} />}
        {tab === 'chart' && <ChartsTab D={D} chartSym={chartSym} setChartSym={setChartSym} watch={watch} />}
        {tab === 'sectors' && <SectorsTab D={D} />}
        {tab === 'highs' && (
          <HighsTab
            D={D} highMode={highMode} setHighMode={setHighMode}
            sectorFilter={sectorFilter} setSectorFilter={setSectorFilter}
            watch={watch} toggle={toggle} onOpen={setDrawerSym}
          />
        )}
        {tab === 'draw' && <DrawdownTab D={D} watch={watch} toggle={toggle} onOpen={setDrawerSym} />}
        {tab === 'watch' && <WatchTab D={D} watch={watch} toggle={toggle} onOpen={setDrawerSym} profile={profile} />}
      </div>

      {drawerStock && (
        <StockDrawer
          stock={drawerStock}
          watch={watch}
          toggle={toggle}
          profile={profile}
          onClose={() => setDrawerSym(null)}
        />
      )}
    </div>
  );
}
