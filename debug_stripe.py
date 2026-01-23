import stripe
import sys

try:
    print(f"Stripe Version (start): {getattr(stripe, 'version', 'N/A')}")
    print(f"Stripe Version (__version__): {getattr(stripe, '__version__', 'N/A')}")
except Exception as e:
    print(e)

try:
    print("Directly checking stripe.Invoice.upcoming:")
    print(stripe.Invoice.upcoming)
except AttributeError:
    print("stripe.Invoice has no attribute 'upcoming'")

try:
    print("Checking stripe.Invoice attributes:")
    print(dir(stripe.Invoice))
except Exception as e:
    print(f"Error checking dir: {e}")
