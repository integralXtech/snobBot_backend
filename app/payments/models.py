"""Pydantic models for payment endpoints."""

from pydantic import BaseModel
from typing import Optional, List


class AddCardRequest(BaseModel):
    payment_method_id: str


class AddCardResponse(BaseModel):
    message: str
    card_brand: str
    card_last4: str
    verified: bool


class PaymentMethodResponse(BaseModel):
    id: str
    card_brand: str
    card_last4: str
    card_exp_month: int
    card_exp_year: int
    is_default: bool
    verified: bool
    created_at: str


class SubscribeRequest(BaseModel):
    plan_id: str
    coupon_code: Optional[str] = None


class SubscribeResponse(BaseModel):
    message: str
    subscription_id: str
    plan_id: str
    status: str
    amount_paid: float
    tax_paid: float
    discount_applied: float
    currency: str
    current_period_end: Optional[str] = None


class SubscriptionResponse(BaseModel):
    id: str
    plan_id: str
    plan_name: str
    status: str
    current_period_start: Optional[str]
    current_period_end: Optional[str]
    cancel_at_period_end: bool


class PlanResponse(BaseModel):
    id: str
    name: str
    price: float
    currency: str
    interval: str
    features: List[str]
    active: bool


class AddonResponse(BaseModel):
    id: str
    name: str
    price: float
    currency: str
    description: str
    active: bool


class PlansAndAddonsResponse(BaseModel):
    plans: List[PlanResponse]
    addons: List[AddonResponse]


class ValidateCouponRequest(BaseModel):
    coupon_code: str
    plan_id: str


class ValidateCouponResponse(BaseModel):
    valid: bool
    message: str
    discount_type: Optional[str] = None # "percent" or "amount"
    discount_amount: Optional[float] = None
    new_price: Optional[float] = None


class CreditLimits(BaseModel):
    blog_ideas_credits_total: int
    blog_ideas_credits_used: int
    faq_credits_total: int
    faq_credits_used: int
    blog_creation_credits_total: int
    blog_creation_credits_used: int
    chatbot_messages_credits_total: int
    chatbot_messages_credits_used: int
    chatbot_training_credits_total: int
    chatbot_training_credits_used: int
    chatbot_count_allowed: int
    white_label_allowed: bool


class UserUsageResponse(BaseModel):
    active_subscriptions: List[SubscriptionResponse]
    credits: Optional[CreditLimits]


class StripeConfigResponse(BaseModel):
    publishable_key: str
    environment: str
