import { useMemo, useState } from 'react';

export type SortDir = 'asc' | 'desc';

export interface SortState { key: string; dir: SortDir }

// A column is sortable when it exposes a value accessor. `dir` is the
// direction applied on first click — numeric columns read best high-first,
// text columns A-Z.
export interface SortSpec<Row> {
  key: string;
  value: (row: Row) => number | string;
  first?: SortDir;
}

export function useSort<Row>(rows: Row[], specs: SortSpec<Row>[], initial: SortState) {
  const [sort, setSort] = useState<SortState>(initial);

  const sorted = useMemo(() => {
    const spec = specs.find(s => s.key === sort.key);
    if (!spec) return rows;
    const sign = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = spec.value(a), bv = spec.value(b);
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv)) * sign;
      }
      return (av - bv) * sign;
    });
    // specs are rebuilt each render but are stable in shape; key off sort + rows.
  }, [rows, sort.key, sort.dir]);

  const onSort = (key: string) => {
    const spec = specs.find(s => s.key === key);
    if (!spec) return;
    // rows may be empty under an active filter, so probe only when there is a row.
    const firstDir: SortDir = spec.first
      || (rows.length && typeof spec.value(rows[0]) === 'string' ? 'asc' : 'desc');
    setSort(prev => prev.key === key
      ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: firstDir });
  };

  return { sorted, sort, onSort };
}

export function sortLabel(sort: SortState, cols: { key: string; label: string }[]) {
  const c = cols.find(x => x.key === sort.key);
  if (!c) return '';
  return 'sorted by ' + c.label.toLowerCase() + (sort.dir === 'desc' ? ' (high → low)' : ' (low → high)');
}
