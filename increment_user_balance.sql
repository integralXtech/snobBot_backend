-- Run this in your Supabase SQL Editor to enable credit increments
CREATE OR REPLACE FUNCTION increment_user_balance(
    target_user_id UUID,
    add_messages_credits INTEGER DEFAULT 0,
    add_training_credits INTEGER DEFAULT 0,
    add_chatbot_count INTEGER DEFAULT 0,
    add_blog_creation INTEGER DEFAULT 0,
    add_blog_ideas INTEGER DEFAULT 0,
    add_faq INTEGER DEFAULT 0,
    is_renewal BOOLEAN DEFAULT FALSE,
    set_white_label BOOLEAN DEFAULT FALSE
) RETURNS VOID AS $$
BEGIN
    INSERT INTO user_usage_balances (
        user_id, 
        chatbot_messages_credits_total, 
        chatbot_training_credits_total, 
        chatbot_count_allowed,
        blog_creation_credits_total,
        blog_ideas_credits_total,
        faq_credits_total
    )
    VALUES (
        target_user_id, 
        add_messages_credits, 
        add_training_credits, 
        add_chatbot_count,
        add_blog_creation,
        add_blog_ideas,
        add_faq
    )
    ON CONFLICT (user_id) DO UPDATE SET
        chatbot_messages_credits_total = CASE WHEN is_renewal THEN add_messages_credits ELSE user_usage_balances.chatbot_messages_credits_total + add_messages_credits END,
        chatbot_training_credits_total = CASE WHEN is_renewal THEN add_training_credits ELSE user_usage_balances.chatbot_training_credits_total + add_training_credits END,
        chatbot_count_allowed = CASE WHEN is_renewal THEN add_chatbot_count ELSE user_usage_balances.chatbot_count_allowed + add_chatbot_count END,
        blog_creation_credits_total = CASE WHEN is_renewal THEN add_blog_creation ELSE user_usage_balances.blog_creation_credits_total + add_blog_creation END,
        blog_ideas_credits_total = CASE WHEN is_renewal THEN add_blog_ideas ELSE user_usage_balances.blog_ideas_credits_total + add_blog_ideas END,
        faq_credits_total = CASE WHEN is_renewal THEN add_faq ELSE user_usage_balances.faq_credits_total + add_faq END,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;
