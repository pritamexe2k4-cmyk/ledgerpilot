"""LedgerPilot API (Day 1 skeleton: health check only)."""
import os

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

app = FastAPI(
    title="LedgerPilot API",
    description="GenAI invoice-to-ledger agent for R2R / AP automation (sample project).",
    version="0.1.0",
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness probe used by deploys and the local smoke test."""
    return {
        "status": "ok",
        "service": "ledgerpilot-api",
        "version": app.version,
        "env": os.getenv("APP_ENV", "dev"),
    }
