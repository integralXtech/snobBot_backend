-- RUN THIS IN SUPABASE SQL EDITOR TO APPLY THE HARDENED FIX
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
        faq_credits_total,
        updated_at
    )
    VALUES (
        target_user_id, 
        add_messages_credits, 
        add_training_credits, 
        add_chatbot_count,
        add_blog_creation,
        add_blog_ideas,
        add_faq,
        NOW()
    )
    ON CONFLICT (user_id) DO UPDATE SET
        chatbot_messages_credits_total = CASE 
            WHEN is_renewal THEN add_messages_credits 
            ELSE COALESCE(user_usage_balances.chatbot_messages_credits_total, 0) + add_messages_credits 
        END,
        chatbot_training_credits_total = CASE 
            WHEN is_renewal THEN add_training_credits 
            ELSE COALESCE(user_usage_balances.chatbot_training_credits_total, 0) + add_training_credits 
        END,
        chatbot_count_allowed = CASE 
            WHEN is_renewal THEN add_chatbot_count 
            ELSE COALESCE(user_usage_balances.chatbot_count_allowed, 0) + add_chatbot_count 
        END,
        blog_creation_credits_total = CASE 
            WHEN is_renewal THEN add_blog_creation 
            ELSE COALESCE(user_usage_balances.blog_creation_credits_total, 0) + add_blog_creation 
        END,
        blog_ideas_credits_total = CASE 
            WHEN is_renewal THEN add_blog_ideas 
            ELSE COALESCE(user_usage_balances.blog_ideas_credits_total, 0) + add_blog_ideas 
        END,
        faq_credits_total = CASE 
            WHEN is_renewal THEN add_faq 
            ELSE COALESCE(user_usage_balances.faq_credits_total, 0) + add_faq 
        END,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;
