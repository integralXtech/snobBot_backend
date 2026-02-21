-- Migration to add Stripe Connect related columns to the agencies table
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agencies' AND column_name = 'stripe_connected_at') THEN
        ALTER TABLE agencies ADD COLUMN stripe_connected_at TIMESTAMPTZ;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agencies' AND column_name = 'stripe_account_email') THEN
        ALTER TABLE agencies ADD COLUMN stripe_account_email TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agencies' AND column_name = 'stripe_account_status') THEN
        ALTER TABLE agencies ADD COLUMN stripe_account_status TEXT DEFAULT 'inactive';
    END IF;
END $$;
