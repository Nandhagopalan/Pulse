import { useEffect, useState } from 'react';
import { T, dirColor } from './theme';
import { areaLine } from './lib/svg';
import { useProfile } from './lib/profile';
import { useRoute, buildHash } from './lib/router';
import { useMarket } from './lib/useMarket';
import { addToWatchlist, fetchWatchlist, importWatchlist, logout, removeFromWatchlist } from './lib/api';
import { LoginGate, BootstrapScreen } from './components/LoginGate';
import { Mono, Sparkline, ghostBtn, inkBtn, inputStyle } from './components/ui';
import { Sidebar, type NavItem } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { StockDrawer } from './components/StockDrawer';
import { BreadthTab, RANGES } from './components/tabs/BreadthTab';
import type { BreadthRange } from './components/tabs/BreadthTab';
import { ChartsTab } from './components/tabs/ChartsTab';
import { SectorsTab } from './components/tabs/SectorsTab';
import { HighsTab } from './components/tabs/HighsTab';
import type { HighMode } from './components/tabs/HighsTab';
import { DrawdownTab } from './components/tabs/DrawdownTab';
import { WatchTab } from './components/tabs/WatchTab';
import { NewsTab } from './components/tabs/NewsTab';

const DEFAULT_QUOTES = [
  'The trend is your friend until the end when it bends.',
  'Trade the setup, not the P&L.',
  'When breadth narrows, tighten stops — the market whispers before it shouts.',
  'Buy strength in strong sectors; never average down a swing trade.',
  'No setup, no trade. Cash is a position.',
];

type TabId = 'breadth' | 'chart' | 'sectors' | 'highs' | 'draw' | 'watch' | 'news';
type CardMode = 'area' | 'bar';

const TAB_META: Record<TabId, { label: string; sub: string; slug: string }> = {
  breadth: { label: 'Breadth', sub: 'Market participation & internals', slug: 'breadth' },
  chart: { label: 'Charts', sub: 'Price action with EMAs', slug: 'charts' },
  sectors: { label: 'Sectors', sub: 'Rotation & relative strength', slug: 'sectors' },
  highs: { label: 'Highs', sub: 'Fresh highs & breakouts', slug: 'highs' },
  draw: { label: 'Drawdown', sub: 'How far stocks sit off their peaks', slug: 'drawdown' },
  watch: { label: 'Watchlist', sub: 'Your swing candidates', slug: 'watchlist' },
  news: { label: 'News', sub: 'Headlines for your watchlist', slug: 'news' },
};

const TAB_IDS = Object.keys(TAB_META) as TabId[];
const SLUG_TO_TAB: Record<string, TabId> = Object.fromEntries(
  TAB_IDS.map(id => [TAB_META[id].slug, id]),
);

const HIGH_MODES: HighMode[] = ['w52', 'ath', 'wk'];
const DEFAULT_HIGH_MODE: HighMode = 'w52';
const DEFAULT_CHART_SYM = 'NIFTY 50';
const ALL_SECTORS = 'All sectors';
const DEFAULT_BREADTH_RANGE: BreadthRange = '1m';

// Watchlists used to live in localStorage, before accounts existed. The first
// authenticated load migrates whatever is still there onto the account, then
// clears it so the browser copy can never drift from the server's.
const LEGACY_WATCHLIST_KEY = 'pulse-watchlist';

const toWatchMap = (symbols: string[]): Record<string, true> =>
  Object.fromEntries(symbols.map(s => [s, true as const]));

function readLegacyWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(LEGACY_WATCHLIST_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? Object.keys(parsed) : [];
  } catch {
    return [];
  }
}

function useWatchlist(authed: boolean) {
  const [watch, setWatch] = useState<Record<string, true>>({});

  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    (async () => {
      try {
        const legacy = readLegacyWatchlist();
        const res = legacy.length ? await importWatchlist(legacy) : await fetchWatchlist();
        try { localStorage.removeItem(LEGACY_WATCHLIST_KEY); } catch { /* ignore */ }
        if (!cancelled) setWatch(toWatchMap(res.symbols));
      } catch { /* leave empty — the watchlist tab shows its empty state */ }
    })();
    return () => { cancelled = true; };
  }, [authed]);

  const toggle = (sym: string) => {
    const starred = !!watch[sym];
    const previous = watch;
    // Optimistic: starring should feel instant, and the server's reply is the
    // authority a moment later.
    setWatch(prev => {
      const next = { ...prev };
      if (starred) delete next[sym]; else next[sym] = true;
      return next;
    });
    (starred ? removeFromWatchlist(sym) : addToWatchlist(sym))
      .then(res => setWatch(toWatchMap(res.symbols)))
      .catch(() => setWatch(previous));
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
  const [collapsed, setCollapsed] = useState(false);
  const [addingQuote, setAddingQuote] = useState(false);
  const [quoteDraft, setQuoteDraft] = useState('');

  const { route, navigate } = useRoute();
  const { quoteText, shuffle, addQuote } = useQuotes();
  const market = useMarket();
  const { watch, toggle } = useWatchlist(market.auth === 'authed');
  const { profile, update } = useProfile(market.user);

  // The URL is the single source of truth for which page is showing and how it
  // is filtered, so every view is reachable by link and survives a reload.
  const tab = SLUG_TO_TAB[route.segments[0]] ?? 'breadth';
  const chartSym = tab === 'chart' ? route.segments[1] || DEFAULT_CHART_SYM : DEFAULT_CHART_SYM;
  const routeHighMode = route.segments[1] as HighMode | undefined;
  const highMode = tab === 'highs' && routeHighMode && HIGH_MODES.includes(routeHighMode)
    ? routeHighMode
    : DEFAULT_HIGH_MODE;
  const sectorFilter = route.query.get('sector') || ALL_SECTORS;
  const drawerSym = route.query.get('stock');

  // Breadth history window (?range=1m); unknown values fall back to the default.
  const routeRange = route.query.get('range') as BreadthRange | null;
  const breadthRange: BreadthRange = routeRange && RANGES.some(r => r.id === routeRange)
    ? routeRange
    : DEFAULT_BREADTH_RANGE;

  // Breadth card chart types ride in one compact param: ?m=newHighs:area,up20:bar
  const cardMode: Record<string, CardMode> = {};
  for (const part of (route.query.get('m') || '').split(',')) {
    const [id, mode] = part.split(':');
    if (id && (mode === 'area' || mode === 'bar')) cardMode[id] = mode;
  }

  // Unknown or bare hashes settle on a canonical URL without adding history.
  const canonicalUnknown = !route.segments.length || !SLUG_TO_TAB[route.segments[0]];
  useEffect(() => {
    if (canonicalUnknown) navigate(buildHash(['breadth']), { replace: true });
  }, [canonicalUnknown, navigate]);

  const go = (id: TabId) => {
    // Sub-path and filters belong to the page being left, so drop them.
    if (id === 'highs') navigate(buildHash(['highs', DEFAULT_HIGH_MODE]));
    else if (id === 'chart') navigate(buildHash(['charts', DEFAULT_CHART_SYM]));
    else navigate(buildHash([TAB_META[id].slug]));
  };
  const setChartSym = (sym: string) => navigate(buildHash(['charts', sym]));
  const setHighMode = (m: HighMode) => {
    const sector = route.query.get('sector');
    navigate(buildHash(['highs', m], { sector }), { replace: true });
  };
  const setSectorFilter = (s: string) => {
    const rest = route.segments.map(encodeURIComponent).join('/');
    const params = new URLSearchParams(route.query);
    if (!s || s === ALL_SECTORS) params.delete('sector'); else params.set('sector', s);
    const qs = params.toString();
    navigate('#/' + rest + (qs ? '?' + qs : ''), { replace: true });
  };
  const setDrawerSym = (sym: string | null) => {
    const rest = route.segments.map(encodeURIComponent).join('/');
    const params = new URLSearchParams(route.query);
    if (sym) params.set('stock', sym); else params.delete('stock');
    const qs = params.toString();
    navigate('#/' + rest + (qs ? '?' + qs : ''), { replace: !sym });
  };

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
  const watchCount = Object.keys(watch).length;

  const setCard = (id: string, m: CardMode) => {
    const next = { ...cardMode, [id]: m };
    const encoded = Object.entries(next).map(([k, v]) => k + ':' + v).join(',');
    const params = new URLSearchParams(route.query);
    params.set('m', encoded);
    navigate(buildHash(route.segments, Object.fromEntries(params)), { replace: true });
  };

  const setBreadthRange = (r: BreadthRange) => {
    const params = new URLSearchParams(route.query);
    if (r === DEFAULT_BREADTH_RANGE) params.delete('range'); else params.set('range', r);
    navigate(buildHash(route.segments, Object.fromEntries(params)), { replace: true });
  };

  const saveDraft = () => {
    addQuote(quoteDraft);
    setAddingQuote(false);
    setQuoteDraft('');
  };

  const navItems: NavItem[] = [
    { id: 'breadth', label: 'Breadth', icon: 'breadth' },
    { id: 'chart', label: 'Charts', icon: 'chart' },
    { id: 'sectors', label: 'Sectors', icon: 'sectors' },
    { id: 'highs', label: 'Highs', icon: 'highs' },
    { id: 'draw', label: 'Drawdown', icon: 'draw' },
    { id: 'watch', label: 'Watchlist', icon: 'watch', count: watchCount },
    { id: 'news', label: 'News', icon: 'news' },
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
      open: () => setChartSym(ix.name),
    };
  });

  return (
    <>
      <div className="ambient" aria-hidden="true">
        <div className="blob sky" /><div className="blob rose" /><div className="blob amber" />
      </div>

      <div style={{ display: 'flex', position: 'relative', zIndex: 1, minHeight: '100vh' }}>
        <Sidebar
          items={navItems}
          active={tab}
          onNav={id => go(id as TabId)}
          collapsed={collapsed}
          onToggle={() => setCollapsed(c => !c)}
          userName={market.user?.name ?? profile.name}
          userSub={profile.style}
        />

        <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <TopBar
            title={TAB_META[tab].label}
            profile={profile} updateProfile={update} watchCount={watchCount}
            sessionUser={market.user} dataSource={market.source} asOf={market.asOf}
            onRefresh={() => market.refresh()}
            onLogout={() => { void logout().then(() => window.location.reload()); }}
          />

          <div style={{ padding: '22px 24px 72px', maxWidth: 1200, width: '100%', margin: '0 auto' }}>
            {/* Index strip */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
              {indices.map(ix => (
                <div key={ix.name} onClick={ix.open} title="Open chart" style={{ minWidth: 0, cursor: 'pointer', background: T.card, borderRadius: 14, padding: '12px 14px', boxShadow: T.shadow + ', inset 0 0 0 1px ' + T.borderSoft }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.03em', color: T.faint, textTransform: 'uppercase', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{ix.name}</span>
                    <Mono size={11} weight={600} color={ix.color} style={{ whiteSpace: 'nowrap' }}>{ix.chg}</Mono>
                  </div>
                  <Mono size={15} weight={600} style={{ display: 'block', marginTop: 4 }}>{ix.value}</Mono>
                  <div style={{ marginTop: 7 }}>
                    <Sparkline line={ix.line} area={ix.area} color={ix.color} fill={ix.fill} height={30} />
                  </div>
                </div>
              ))}
            </div>

            {/* Rule of the day */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 18, padding: '11px 16px', background: T.card, borderRadius: 12, boxShadow: T.shadow + ', inset 0 0 0 1px ' + T.borderSoft }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', color: T.amber }}>RULE</span>
              <div style={{ fontSize: 13.5, color: T.text, fontStyle: 'italic', flex: 1, minWidth: 0 }}>&ldquo;{quoteText}&rdquo;</div>
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

            {tab === 'breadth' && (
              <BreadthTab
                D={D} cardMode={cardMode} setCard={setCard}
                range={breadthRange} setRange={setBreadthRange}
              />
            )}
            {tab === 'chart' && <ChartsTab D={D} chartSym={chartSym} setChartSym={setChartSym} watch={watch} />}
            {tab === 'sectors' && <SectorsTab D={D} route={route} navigate={navigate} watch={watch} toggle={toggle} onOpen={setDrawerSym} />}
            {tab === 'highs' && (
              <HighsTab
                D={D} highMode={highMode} setHighMode={setHighMode}
                sectorFilter={sectorFilter} setSectorFilter={setSectorFilter}
                watch={watch} toggle={toggle} onOpen={setDrawerSym}
              />
            )}
            {tab === 'draw' && <DrawdownTab D={D} route={route} navigate={navigate} watch={watch} toggle={toggle} onOpen={setDrawerSym} />}
            {tab === 'watch' && <WatchTab D={D} watch={watch} toggle={toggle} onOpen={setDrawerSym} profile={profile} />}
            {tab === 'news' && <NewsTab D={D} route={route} navigate={navigate} watch={watch} />}
          </div>
        </main>
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
    </>
  );
}
