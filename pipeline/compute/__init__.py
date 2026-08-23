"""
Curated lake → the published snapshot.

`analytics` reads the Parquet through DuckDB and computes one session's state;
`publish` is the only writer to Supabase, which is the only store in the app's
request path.
"""
