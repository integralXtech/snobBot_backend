-- Migration to add plan_id column to registered_users table
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'registered_users' AND column_name = 'plan_id') THEN
        ALTER TABLE registered_users ADD COLUMN plan_id UUID REFERENCES agency_plans(id) ON DELETE SET NULL;
    END IF;
END $$;
