-- Russia-hosted PostgreSQL 15+ billing ledger.
CREATE TABLE IF NOT EXISTS billing_accounts (
  id uuid PRIMARY KEY,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','blocked','closed')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS billing_identities (
  account_id uuid PRIMARY KEY REFERENCES billing_accounts(id) ON DELETE CASCADE,
  email_hash char(64) NOT NULL UNIQUE,
  email_ciphertext text NOT NULL,
  email_verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS trial_grants (
  account_id uuid PRIMARY KEY REFERENCES billing_accounts(id) ON DELETE CASCADE,
  credits integer NOT NULL CHECK (credits > 0),
  identity_fingerprint_hash char(64) NOT NULL UNIQUE,
  granted_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payment_orders (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES billing_accounts(id),
  provider text NOT NULL,
  pack_code text NOT NULL,
  credits integer NOT NULL CHECK (credits > 0),
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),
  currency char(3) NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','paid','failed','refunded','cancelled')),
  provider_transaction_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  paid_at timestamptz,
  UNIQUE(provider, provider_transaction_id)
);
CREATE TABLE IF NOT EXISTS payment_events (
  provider text NOT NULL,
  event_id text NOT NULL,
  event_type text NOT NULL,
  payload_sha256 char(64) NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  status text NOT NULL DEFAULT 'received',
  PRIMARY KEY(provider,event_id)
);
CREATE TABLE IF NOT EXISTS credit_ledger (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES billing_accounts(id),
  delta integer NOT NULL CHECK (delta <> 0),
  reason text NOT NULL,
  external_ref text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(account_id, reason, external_ref)
);
CREATE TABLE IF NOT EXISTS credit_reservations (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES billing_accounts(id),
  job_id uuid NOT NULL UNIQUE,
  credits integer NOT NULL CHECK (credits > 0),
  status text NOT NULL CHECK (status IN ('reserved','committed','released')),
  reserved_at timestamptz NOT NULL DEFAULT now(),
  settled_at timestamptz,
  settlement_reason text
);
CREATE TABLE IF NOT EXISTS generation_costs (
  job_id uuid PRIMARY KEY,
  account_id uuid REFERENCES billing_accounts(id),
  provider text NOT NULL,
  provider_model text,
  request_count integer NOT NULL CHECK (request_count >= 0),
  exact_cost_usd numeric(14,8),
  exact_cost_complete boolean NOT NULL DEFAULT false,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_account_created ON credit_ledger(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payment_orders_account_created ON payment_orders(account_id, created_at DESC);
