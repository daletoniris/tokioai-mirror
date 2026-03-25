"""
FastAPI Server — REST API + WebSocket for TokioAI Agent.

Endpoints:
    GET  /health          — Health check
    GET  /stats           — Agent statistics
    GET  /tools           — List available tools
    POST /chat            — Send message (sync)
    WS   /ws              — WebSocket interactive session
    GET  /sessions         — List sessions
    GET  /sessions/{id}   — Get session details
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from ..engine.agent import TokioAgent
from ..engine.entity_sync import on_security_event, security_dashboard

logger = logging.getLogger(__name__)

# ── API Authentication ──
# Set TOKIO_API_KEY in .env to enable authentication.
# If not set, the API runs without auth (development mode).
_API_KEY = os.getenv("TOKIO_API_KEY", "")

# ── Rate Limiting ──
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = int(os.getenv("TOKIO_RATE_LIMIT", "60"))  # requests per window
_rate_limit_store: dict = defaultdict(list)  # ip -> [timestamps]


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication and rate limiting."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for health check and docs
        if path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        # ── API Key Authentication ──
        if _API_KEY:
            auth_header = request.headers.get("Authorization", "")
            api_key_header = request.headers.get("X-API-Key", "")

            # Accept Bearer token or X-API-Key header
            provided_key = ""
            if auth_header.startswith("Bearer "):
                provided_key = auth_header[7:]
            elif api_key_header:
                provided_key = api_key_header

            if not provided_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API key required. Use Authorization: Bearer <key> or X-API-Key header."},
                )

            if not secrets.compare_digest(provided_key, _API_KEY):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid API key."},
                )

        # ── Rate Limiting ──
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Clean old entries
        _rate_limit_store[client_ip] = [
            ts for ts in _rate_limit_store[client_ip]
            if now - ts < _RATE_LIMIT_WINDOW
        ]

        if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_MAX:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {_RATE_LIMIT_MAX} requests per {_RATE_LIMIT_WINDOW}s."},
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )

        _rate_limit_store[client_ip].append(now)

        return await call_next(request)


# Global agent instance
_agent: Optional[TokioAgent] = None


def get_agent() -> TokioAgent:
    global _agent
    if _agent is None:
        _agent = TokioAgent()
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize agent on startup."""
    logger.info("🚀 Starting TokioAI Server...")
    if _API_KEY:
        logger.info("🔒 API authentication enabled")
    else:
        logger.warning("⚠️ API authentication DISABLED — set TOKIO_API_KEY in .env for production")
    get_agent()
    yield
    logger.info("👋 TokioAI Server shutting down")


app = FastAPI(
    title="TokioAI Agent API",
    version="2.0.0",
    description="Autonomous AI Agent API",
    lifespan=lifespan,
)

# Auth + Rate limit middleware
app.add_middleware(AuthRateLimitMiddleware)

# CORS — configurable via environment (NOT allow_origins=["*"])
allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8080").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
)


# ── Models ──

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    images: Optional[List[Dict[str, str]]] = None  # [{"data": base64, "media_type": "image/..."}]


class ChatResponse(BaseModel):
    response: str
    session_id: str


# ── Endpoints ──

@app.get("/health")
async def health():
    agent = get_agent()
    return {
        "status": "healthy",
        "version": "2.0.0",
        "llm": agent.llm.display_name(),
        "tools": agent.registry.count(),
    }


@app.get("/stats")
async def stats():
    return get_agent().get_stats()


@app.get("/tools")
async def list_tools():
    agent = get_agent()
    return {
        "count": agent.registry.count(),
        "tools": [t.to_dict() for t in agent.registry.list_all()],
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    agent = get_agent()
    session_id = request.session_id or agent.session_manager.create_session()

    try:
        response = await agent.process_message(
            user_message=request.message,
            session_id=session_id,
            images=request.images,
        )
        return ChatResponse(response=response, session_id=session_id)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Entity Events (push from Raspi) ──

class EntityEvent(BaseModel):
    type: str  # wifi_attack, ble_attack, face_detected, service_down, etc.
    attack_type: Optional[str] = None
    message: str = ""
    severity: str = "info"
    attacker: Optional[str] = None
    data: Optional[Dict] = None


@app.post("/entity/event")
async def entity_event(event: EntityEvent):
    """Receive events pushed from the Raspi Entity.

    WiFi attacks, BLE attacks, face detections, service status, etc.
    The core processes them: logs, triggers router defense, notifies via Telegram.
    """
    logger.info(f"Entity event: {event.type} [{event.severity}] {event.message}")

    # Track in security dashboard
    if event.type in ("wifi_attack", "ble_attack", "network_scan"):
        await on_security_event(
            event_type=event.type,
            message=event.message,
            severity=event.severity,
            attacker=event.attacker or "",
        )

    # TODO Phase 5: trigger router defense for wifi_attack
    # TODO: send Telegram notification for critical events

    return {"ok": True, "type": event.type, "blocked_today": security_dashboard.blocked_today}


@app.get("/security/dashboard")
async def security_dash():
    """Security dashboard data — attacks blocked, recent events."""
    return security_dashboard.get_summary()


@app.get("/self-healing/status")
async def self_healing_status():
    """Get self-healing engine status — all monitored services."""
    agent = get_agent()
    return {
        "services": agent.self_healing.get_status(),
        "recent_actions": agent.self_healing.get_log(20),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    agent = get_agent()
    session_id = agent.session_manager.create_session()

    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "llm": agent.llm.display_name(),
        "tools": agent.registry.count(),
    })

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
                user_message = msg.get("message", data)
            except json.JSONDecodeError:
                user_message = data

            if not user_message.strip():
                continue

            # Send thinking indicator
            await websocket.send_json({
                "type": "thinking",
                "message": "Procesando...",
            })

            try:
                response = await agent.process_message(
                    user_message=user_message,
                    session_id=session_id,
                )

                await websocket.send_json({
                    "type": "response",
                    "message": response,
                    "session_id": session_id,
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Error: {e}",
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")


@app.get("/sessions")
async def list_sessions():
    return get_agent().session_manager.list_sessions()


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = get_agent().session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
