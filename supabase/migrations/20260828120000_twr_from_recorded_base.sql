-- Restate every session factor against the equity that actually preceded it.
--
-- `twr_factor` was derived by the nightly run from the book state it had just
-- rebuilt from storage. On the rules book that state is the previous session's
-- close and the two agree exactly. On a manual book they do not: the API adds
-- and closes positions between runs, so the rebuild already holds a position
-- the previous session did not, valued at that session's close while the cash
-- that bought it has gone. The entry-to-yesterday gain therefore landed in the
-- denominator and was never counted as return — a backdated trade entered the
-- book already up and the book never said so. Three such entries cost the
-- manual book 1.48 points of a 2.01% return.
--
-- The recorded equity of the preceding session is the honest base, and cash
-- flows keep their place in it, so a deposit still never reads as performance.
-- Re-running this is a no-op, and on the rules book it reproduces the stored
-- factors to the last bit.
UPDATE strategy_state s
   SET twr_factor = p.equity / (p.prev_equity + s.net_flow)
  FROM (
        SELECT book_id, date, equity,
               LAG(equity) OVER (PARTITION BY book_id ORDER BY date) AS prev_equity
          FROM strategy_state
       ) p
 WHERE p.book_id = s.book_id
   AND p.date    = s.date
   AND p.prev_equity IS NOT NULL
   AND p.prev_equity + s.net_flow > 0;
