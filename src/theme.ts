// LearnVerse design language — warm off-white paper, cool ink scale, one blue brand.
// Token names are kept stable so every tab re-skins from this single source.
// green/red are reserved strictly for price direction; amber for accent highlights.
export const T = {
  bg: '#f7f6f3',
  card: '#ffffff',
  cardAlt: '#f5f6f8',
  border: '#e6e8ec',
  borderSoft: '#eef0f3',

  ink: '#0b1220',
  // "navy" token is mapped to the brand blue so existing usages become brand-accented.
  navy: '#246db1',
  navySoft: 'rgba(36,109,177,0.08)',
  text: '#2c3548',
  muted: '#6b7280',
  faint: '#9aa1ad',

  amber: '#a16207',
  amberSoft: 'rgba(161,98,7,0.10)',

  up: '#15803d',
  upSoft: 'rgba(21,128,61,0.10)',
  down: '#b91c1c',
  downSoft: 'rgba(185,28,28,0.10)',

  // brand ramp (explicit, for the new shell)
  brand: '#3184CB',
  brand50: '#eef6fc',
  brand600: '#246db1',
  brand700: '#1d588f',

  serif: "'Public Sans', ui-sans-serif, system-ui, sans-serif",
  sans: "'Public Sans', ui-sans-serif, system-ui, sans-serif",
  mono: "'JetBrains Mono', ui-monospace, monospace",

  shadow: '0 1px 0 rgba(15,23,42,0.04), 0 1px 2px rgba(15,23,42,0.04)',
  shadowPop: '0 8px 24px rgba(15,23,42,0.08), 0 2px 6px rgba(15,23,42,0.04)',
  radius: 14,
} as const;

export const dirColor = (v: number) => (v >= 0 ? T.up : T.down);
