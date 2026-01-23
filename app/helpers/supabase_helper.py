from typing import Any, Dict
import logging
from app.helpers.response_helper import success_response, error_response

logger = logging.getLogger(__name__)

def handle_supabase_error(response: Any, default_error: str = "Unknown error") -> Dict[str, Any]:
    """
    Normalizes Supabase responses into a consistent format.
    Returns either a success_response or error_response.
    """
    # Case 1: Supabase returned an error object
    if hasattr(response, "error") and response.error:
        logger.error(f"Supabase error: {response.error.message}")
        return error_response(
            message=response.error.message,
            code="SUPABASE_ERROR"
        )

    # Case 2: No data returned (like empty insert/select)
    if not getattr(response, "data", None):
        logger.error(f"Supabase response missing data. Default error: {default_error}")
        return error_response(
            message=default_error,
            code="SUPABASE_NO_DATA"
        )

    # Case 3: All good → wrap into success response
    return success_response(
        message="Supabase request successful",
        code="SUPABASE_OK",
        data=response.data
    )
