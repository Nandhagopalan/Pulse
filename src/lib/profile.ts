import { useMemo } from 'react';
import type { SessionUser } from './api';

/**
 * The profile is read-only identity, drawn from the SSO session.
 *
 * It used to carry editable trading preferences too — capital, risk per trade,
 * max positions — which the drawer and watchlist sized from. The strategy book
 * now owns all three: its config is versioned into `strategy_config_log` and
 * stamped onto every position, and its capital is changed (destructively, on
 * purpose) from the Strategy tab. A second, unversioned copy in a profile menu
 * could only drift out of agreement with it, and did.
 */
export interface Profile {
  name: string;
  handle: string;
  email: string;
  style: string;
}

const STYLE = 'Momentum swing · NSE';

function handleFor(user: SessionUser | null): string {
  const local = user?.email?.split('@')[0] ?? '';
  const slug = local.replace(/[^a-z0-9]/gi, '').toLowerCase();
  return slug ? '@' + slug : '@trader';
}

export function useProfile(user: SessionUser | null) {
  const profile = useMemo<Profile>(() => ({
    name: user?.name ?? 'Trader',
    handle: handleFor(user),
    email: user?.email ?? '',
    style: STYLE,
  }), [user]);

  return { profile };
}
