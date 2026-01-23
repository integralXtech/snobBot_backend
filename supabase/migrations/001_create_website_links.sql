-- Create website_links table for storing discovered links
CREATE TABLE IF NOT EXISTS website_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chatbot_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    is_crawled BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Ensure unique URL per chatbot
    CONSTRAINT unique_chatbot_url UNIQUE (chatbot_id, user_id, url)
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_website_links_chatbot ON website_links(chatbot_id, user_id);
CREATE INDEX IF NOT EXISTS idx_website_links_crawled ON website_links(chatbot_id, user_id, is_crawled);
CREATE INDEX IF NOT EXISTS idx_website_links_url ON website_links(url);

-- Add RLS (Row Level Security) policies
ALTER TABLE website_links ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their own links
CREATE POLICY "Users can view own links" ON website_links
    FOR SELECT
    USING (auth.uid()::text = user_id);

-- Policy: Users can insert their own links
CREATE POLICY "Users can insert own links" ON website_links
    FOR INSERT
    WITH CHECK (auth.uid()::text = user_id);

-- Policy: Users can update their own links
CREATE POLICY "Users can update own links" ON website_links
    FOR UPDATE
    USING (auth.uid()::text = user_id);

-- Policy: Users can delete their own links
CREATE POLICY "Users can delete own links" ON website_links
    FOR DELETE
    USING (auth.uid()::text = user_id);
