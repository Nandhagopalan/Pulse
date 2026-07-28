// Ikigai design system — ink enso on warm paper, amber candles.
// One ink, one accent; green/red reserved strictly for price direction.
export const T = {
  bg: '#F4F2EC',
  card: '#FDFCF9',
  cardAlt: '#F8F6F0',
  border: '#E6E1D5',
  borderSoft: '#EFEBE1',

  ink: '#232B38',
  navy: '#2B4066',
  navySoft: 'rgba(43,64,102,0.08)',
  text: '#3D4450',
  muted: '#8C8677',
  faint: '#B5AE9E',

  amber: '#B98A2F',
  amberSoft: 'rgba(185,138,47,0.12)',

  up: '#3A7863',
  upSoft: 'rgba(58,120,99,0.10)',
  down: '#A85A4A',
  downSoft: 'rgba(168,90,74,0.10)',

  serif: "'Fraunces', Georgia, serif",
  sans: "'Albert Sans', 'Helvetica Neue', sans-serif",
  mono: "'IBM Plex Mono', monospace",

  shadow: '0 1px 2px rgba(35,43,56,0.04)',
  radius: 14,
} as const;

export const dirColor = (v: number) => (v >= 0 ? T.up : T.down);
