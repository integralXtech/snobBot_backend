import os
import sys
import asyncio

# Fix for asyncio NotImplementedError on Windows when using subprocesses (Playwright)
# This MUST be set before any event loop is created (e.g. by uvicorn)
if sys.platform == 'win32':
    try:
        # Check if it's already a Proactor event loop policy
        if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

import logging
from contextlib import asynccontextmanager
import subprocess

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.auth import auth_router
from app.RAG.routes import rag_router
from app.s3.routes import s3_router
from app.SEO.routes import seo_router
from app.Blog.routes import blog_router
from app.payments import payments_router
from app.payments.agency_routes import agency_payments_router
from app.agency.routes import agency_router
from app.helpers.response_helper import error_response


# ---------------------------
# PlayWright Installation Helper
# ---------------------------
def ensure_chromium_installed():
    """Ensure Chromium is installed for Playwright."""
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        print("Chromium successfully installed or already available.")
    except subprocess.CalledProcessError:
        print("Failed to install Chromium during runtime.")


# ---------------------------
# Logging Configuration
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------
# FastAPI Lifespan
# ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Starting Snobbots Backend API")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Debug mode: {settings.debug}")

    # Install Chromium asynchronously in a separate thread
    try:
        logger.info("Checking Playwright Chromium installation...")
        await asyncio.to_thread(ensure_chromium_installed)
        logger.info("Playwright Chromium check completed.")
    except Exception as e:
        logger.warning(f"⚠️ Chromium installation failed: {e}")

    yield  # --- app runs here ---

    logger.info("Shutting down Snobbots Backend API")


# ---------------------------
# FastAPI App
# ---------------------------
app = FastAPI(
    title="Snobbots Backend API",
    description="Backend API for Snobbots with Supabase Authentication + OpenAI RAG",
    version="1.0.0",
    debug=settings.debug,
    lifespan=lifespan,
)

import json

# Add CORS middleware
# Parse CORS_ORIGINS from settings (it might be a JSON string or comma-separated)
# We strip potential literal quotes that can appear from .env files
# Add CORS middleware
# Note: To support credentials with "allow all", we use a catch-all regex
# because allow_origins=["*"] is not permitted with allow_credentials=True
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://snobbots-frontend.vercel.app",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------
# Global Exception Handlers
# -------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handles Pydantic validation errors."""
    return JSONResponse(
        status_code=422,
        content=error_response(
            message="Validation failed",
            code="VALIDATION_ERROR",
            errors=exc.errors(),
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handles HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            message=str(exc.detail),
            code="HTTP_ERROR",
        ),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handles uncaught exceptions."""
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=error_response(
            message="Internal server error",
            code="SERVER_ERROR",
        ),
    )


# ---------------------------
# Routers
# ---------------------------
app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(rag_router, prefix=settings.api_prefix)
app.include_router(s3_router, prefix=settings.api_prefix)
app.include_router(seo_router, prefix=settings.api_prefix)
app.include_router(blog_router, prefix=settings.api_prefix)
app.include_router(payments_router, prefix=settings.api_prefix)
app.include_router(agency_payments_router, prefix=settings.api_prefix)
app.include_router(agency_router, prefix=settings.api_prefix)


# ---------------------------
# Root endpoint
# ---------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {
        "message": "Snobbots Backend API",
        "version": "1.0.0",
        "status": "running",
    }


# ---------------------------
# Health Check
# ---------------------------
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.environment,
        "debug": settings.debug,
    }


# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )