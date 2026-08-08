"""
Pipeline configuration.

Reads the same repo-root `.env` / `.env.local` files the Node server reads
(server/src/config.ts), so credentials live in exactly one place. Real
environment variables win over file values — that is what lets the GitHub
Actions workflow inject secrets without a file.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _parse_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if not sep:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


_file_env = {**_parse_env_file(ROOT / ".env"), **_parse_env_file(ROOT / ".env.local")}


def env(key: str, fallback: str = "") -> str:
    return os.environ.get(key) or _file_env.get(key) or fallback


class Config:
    # ── Cloudflare R2 ────────────────────────────────────────────────────────
    r2_account_id = env("R2_ACCOUNT_ID")
    r2_bucket = env("R2_BUCKET_NAME", "pulse-terminal")
    # Cloudflare labels these "Access Key ID" / "Secret Access Key"; accept the
    # shorter names too, since that is what the R2 dashboard copy button emits.
    r2_access_key_id = env("R2_ACCESS_KEY_ID") or env("R2_KEY_ID")
    r2_secret_access_key = env("R2_SECRET_ACCESS_KEY") or env("R2_SECRET_KEY")
    r2_token_value = env("R2_TOKEN_VALUE")

    # ── Supabase (direct Postgres connection, not the REST API) ──────────────
    # Bulk upserts of a few thousand rows go through psycopg; the REST API would
    # need chunking and is far slower for this shape of write.
    supabase_db_url = env("SUPABASE_DB_URL") or env("DATABASE_URL")

    # ── Ingestion window ─────────────────────────────────────────────────────
    history_start = env("HISTORY_START", "2007-01-01")
    nse_delay = float(env("NSE_DELAY", "0.15"))
    nse_timeout = float(env("NSE_TIMEOUT", "45"))

    @property
    def r2_endpoint(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    def s3_credentials(self) -> Tuple[str, str]:
        """
        Access key pair for R2's S3 API.

        Cloudflare shows these once, at token creation. A plain account API token
        (`R2_TOKEN_VALUE`) is NOT sufficient on its own: the S3 secret is its
        SHA-256, but the access key id is the token's *id*, which cannot be
        derived from the token value. Accept the derived secret when the id is
        supplied separately; otherwise fail loudly rather than half-configure
        DuckDB and surface it later as an opaque 401.
        """
        if self.r2_access_key_id and self.r2_secret_access_key:
            return self.r2_access_key_id, self.r2_secret_access_key
        if self.r2_access_key_id and self.r2_token_value:
            return self.r2_access_key_id, hashlib.sha256(self.r2_token_value.encode()).hexdigest()
        raise RuntimeError(
            "R2 S3 credentials missing. Add to .env.local:\n"
            '  R2_ACCESS_KEY_ID="..."\n'
            '  R2_SECRET_ACCESS_KEY="..."\n'
            "From the Cloudflare dashboard: R2 → API → Create API Token, with "
            f"Object Read & Write on bucket '{self.r2_bucket}'. R2_TOKEN_VALUE "
            "alone cannot authenticate against the S3 API."
        )

    def require_supabase(self) -> str:
        if not self.supabase_db_url:
            raise RuntimeError(
                "SUPABASE_DB_URL missing. Supabase dashboard → Project Settings → "
                "Database → Connection string → URI (Session pooler), then add to "
                ".env.local:\n"
                '  SUPABASE_DB_URL="postgresql://postgres.<ref>:<password>'
                '@aws-0-<region>.pooler.supabase.com:5432/postgres"'
            )
        return self.supabase_db_url


config = Config()

# ── R2 object layout ─────────────────────────────────────────────────────────
# raw/      vendor files byte-for-byte as NSE served them. Keeping these means
#           every derived dataset can be rebuilt without re-hitting NSE, whose
#           archives rate-limit hard and have retired old paths before.
# curated/  normalized Parquet — the queryable lake.
RAW_BHAV_PREFIX = "raw/nse/bhavcopy"
RAW_INDEX_PREFIX = "raw/nse/index_close"
CURATED_DAILY = "curated/daily"
CURATED_INDEX = "curated/index_daily"
CURATED_ACTIONS = "curated/corporate_actions"
CURATED_INSTRUMENTS = "curated/instruments"


def s3_uri(key: str) -> str:
    return f"s3://{config.r2_bucket}/{key}"
