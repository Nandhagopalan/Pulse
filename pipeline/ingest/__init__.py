"""
Outside world → curated Parquet.

Each module owns one dataset in the lake and is idempotent: re-running it
converges rather than double-counting, so a missed night is repaired by simply
running it again.
"""
