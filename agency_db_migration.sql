-- 1. CLEAN UP: Drop the redundant columns with the wrong naming convention if they exist
ALTER TABLE agency_plans DROP COLUMN IF EXISTS chatbot_count;
ALTER TABLE agency_plans DROP COLUMN IF EXISTS messages_limit;
ALTER TABLE agency_plans DROP COLUMN IF EXISTS training_chars_limit;
ALTER TABLE agency_plans DROP COLUMN IF EXISTS blog_limit;
ALTER TABLE agency_plans DROP COLUMN IF EXISTS blog_ideas_limit;
ALTER TABLE agency_plans DROP COLUMN IF EXISTS faq_limit;

-- 2. ENSURE CORRECT COLUMNS: Add or update the columns with the 'limit_' naming convention
-- We use DO blocks to handle column addition safely
DO $$ 
BEGIN 
    -- Required Resource Limits
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'limit_chatbots') THEN
        ALTER TABLE agency_plans ADD COLUMN limit_chatbots INTEGER DEFAULT 1;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'limit_messages') THEN
        ALTER TABLE agency_plans ADD COLUMN limit_messages INTEGER DEFAULT 1000;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'limit_training_chars') THEN
        ALTER TABLE agency_plans ADD COLUMN limit_training_chars INTEGER DEFAULT 100000;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'limit_blog_creation') THEN
        ALTER TABLE agency_plans ADD COLUMN limit_blog_creation INTEGER DEFAULT 0;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'limit_blog_ideas') THEN
        ALTER TABLE agency_plans ADD COLUMN limit_blog_ideas INTEGER DEFAULT 0;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'limit_faqs') THEN
        ALTER TABLE agency_plans ADD COLUMN limit_faqs INTEGER DEFAULT 0;
    END IF;

    -- Ensure description and interval exist (Added in previous migration)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'description') THEN
        ALTER TABLE agency_plans ADD COLUMN description TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'agency_plans' AND column_name = 'interval') THEN
        ALTER TABLE agency_plans ADD COLUMN interval TEXT DEFAULT 'month';
    END IF;
END $$;

-- 3. ENSURE NOT NULL: Make mandatory columns NOT NULL where appropriate
-- Note: We only do this if we are sure columns are populated or have defaults
ALTER TABLE agency_plans ALTER COLUMN limit_chatbots SET NOT NULL;
ALTER TABLE agency_plans ALTER COLUMN limit_messages SET NOT NULL;
ALTER TABLE agency_plans ALTER COLUMN limit_training_chars SET NOT NULL;
ALTER TABLE agency_plans ALTER COLUMN limit_blog_creation SET NOT NULL;
ALTER TABLE agency_plans ALTER COLUMN limit_blog_ideas SET NOT NULL;
ALTER TABLE agency_plans ALTER COLUMN limit_faqs SET NOT NULL;

-- 4. TABLES: Ensure agency_topups and agency_subscriptions exist (Same as before)
CREATE TABLE IF NOT EXISTS agency_topups (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    credit_type TEXT CHECK (credit_type IN ('messages', 'characters', 'blogs', 'ideas', 'faqs', 'credits')),
    credit_amount INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agency_subscriptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    customer_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    plan_id UUID REFERENCES agency_plans(id) ON DELETE SET NULL,
    agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE,
    status TEXT CHECK (status IN ('active', 'canceled', 'past_due', 'trialing')),
    current_period_start TIMESTAMPTZ DEFAULT NOW(),
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. RLS
ALTER TABLE agency_topups ENABLE ROW LEVEL SECURITY;
ALTER TABLE agency_subscriptions ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Public Read Topups') THEN
        CREATE POLICY "Public Read Topups" ON agency_topups FOR SELECT USING (true);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Customers Read Own Subscriptions') THEN
        CREATE POLICY "Customers Read Own Subscriptions" ON agency_subscriptions FOR SELECT USING (auth.uid() = customer_id);
    END IF;
END $$;
