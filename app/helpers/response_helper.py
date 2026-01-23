from typing import Any, Dict, Optional, List

def success_response(
    data: Optional[Dict[str, Any]] = None,
    message: str = "Success",
    code: str = "SUCCESS"
) -> Dict[str, Any]:
    return {
        "success": True,
        "message": message,
        "code": code,
        "data": data or {},
        "errors": []
    }

def error_response(
    message: str,
    code: str = "ERROR",
    data: Optional[Dict[str, Any]] = None,
    errors: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    return {
        "success": False,
        "message": message,
        "code": code,
        "data": data or {},
        "errors": errors or []
    }
