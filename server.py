"""
Memos MCP Server

An MCP server that provides tools for interacting with a Memos instance.
Supports searching, creating, and updating memos.

Runs as a Remote MCP Server with Streamable HTTP transport.
Uses OAuth 2.0 Authorization Code flow with PKCE for authentication.
"""

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

# Initialize FastMCP server
mcp = FastMCP("memos")

# Get Memos configuration from environment variables
MEMOS_BASE_URL = os.getenv("MEMOS_BASE_URL", "http://localhost:5230")
MEMOS_API_TOKEN = os.getenv("MEMOS_API_TOKEN", "")

# OAuth 2.0 configuration
OAUTH_PASSWORD = os.getenv("OAUTH_PASSWORD", "")
OAUTH_ISSUER_URL = os.getenv("OAUTH_ISSUER_URL", "")  # If empty, auto-detect from request
OAUTH_TOKEN_EXPIRY_SECONDS = int(os.getenv("OAUTH_TOKEN_EXPIRY_SECONDS", "3600"))
OAUTH_TOKEN_STORAGE_PATH = os.getenv("OAUTH_TOKEN_STORAGE_PATH", "")  # Path to persist tokens (optional)


def get_issuer_url(request: Request) -> str:
    """Get the OAuth issuer URL, auto-detecting from request if not configured."""
    if OAUTH_ISSUER_URL:
        return OAUTH_ISSUER_URL
    # Auto-detect from request: use X-Forwarded headers if behind a proxy
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    # Always use HTTPS if behind a proxy (critical for Claude.ai compatibility)
    if request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-host"):
        proto = "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", request.url.netloc)
    return f"{proto}://{host}"

# In-memory OAuth storage (persisted to disk if OAUTH_TOKEN_STORAGE_PATH is set)
registered_clients: dict = {}      # client_id -> client metadata
authorization_codes: dict = {}     # code -> {client_id, redirect_uri, code_challenge, expires_at, scope}
access_tokens: dict = {}           # token -> {client_id, expires_at, scope}
refresh_tokens: dict = {}          # refresh_token -> {client_id, scope}


def _load_tokens_from_disk():
    """Load persisted tokens from disk if storage path is configured."""
    global registered_clients, access_tokens, refresh_tokens
    if not OAUTH_TOKEN_STORAGE_PATH:
        return
    storage_path = Path(OAUTH_TOKEN_STORAGE_PATH)
    if not storage_path.exists():
        return
    try:
        with open(storage_path) as f:
            data = json.load(f)
        # Convert expires_at strings back to datetime objects
        for token, token_data in data.get("access_tokens", {}).items():
            if "expires_at" in token_data:
                token_data["expires_at"] = datetime.fromisoformat(token_data["expires_at"])
        registered_clients.update(data.get("registered_clients", {}))
        access_tokens.update(data.get("access_tokens", {}))
        refresh_tokens.update(data.get("refresh_tokens", {}))
        print(f"Loaded {len(access_tokens)} access tokens and {len(refresh_tokens)} refresh tokens from {storage_path}")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Warning: Failed to load tokens from {storage_path}: {e}")


def _save_tokens_to_disk():
    """Persist tokens to disk if storage path is configured."""
    if not OAUTH_TOKEN_STORAGE_PATH:
        return
    storage_path = Path(OAUTH_TOKEN_STORAGE_PATH)
    # Ensure parent directory exists
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    # Convert datetime objects to ISO strings for JSON serialization
    serializable_access_tokens = {}
    for token, token_data in access_tokens.items():
        serializable_access_tokens[token] = {
            **token_data,
            "expires_at": token_data["expires_at"].isoformat() if "expires_at" in token_data else None,
        }
    data = {
        "registered_clients": registered_clients,
        "access_tokens": serializable_access_tokens,
        "refresh_tokens": refresh_tokens,
    }
    try:
        with open(storage_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Saved {len(access_tokens)} access tokens and {len(refresh_tokens)} refresh tokens to {storage_path}")
    except OSError as e:
        print(f"Warning: Failed to save tokens to {storage_path}: {e}")


# Load tokens on startup
if OAUTH_TOKEN_STORAGE_PATH:
    print(f"Token persistence enabled: {OAUTH_TOKEN_STORAGE_PATH}")
    _load_tokens_from_disk()
else:
    print("Token persistence disabled (OAUTH_TOKEN_STORAGE_PATH not set)")


def get_headers() -> dict:
    """Get headers for API requests including authentication"""
    headers = {
        "Content-Type": "application/json",
    }
    if MEMOS_API_TOKEN:
        headers["Authorization"] = f"Bearer {MEMOS_API_TOKEN}"
    return headers


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE code_verifier matches code_challenge using S256 method."""
    if not code_verifier or not code_challenge:
        return False
    # SHA256 hash of code_verifier, then base64url encode (no padding)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


class OAuthAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate incoming MCP client connections via OAuth 2.0 access tokens."""

    PUBLIC_PATHS = (
        "/health",
        "/.well-known/",
        "/authorize",
        "/token",
        "/register",
    )

    async def dispatch(self, request: Request, call_next):
        # Allow OPTIONS requests through for CORS preflight (no auth required)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public endpoints without auth
        if any(request.url.path.startswith(p) for p in self.PUBLIC_PATHS):
            return await call_next(request)

        # Require Bearer token for MCP endpoint (root path, not matching any public path)
        if request.url.path == "/":
            issuer_url = get_issuer_url(request)
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    {"error": "unauthorized", "error_description": "Missing Authorization header"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{issuer_url}/.well-known/oauth-protected-resource"'
                    },
                )

            token = auth_header[7:]  # Remove "Bearer " prefix
            token_data = access_tokens.get(token)

            if not token_data:
                return JSONResponse(
                    {"error": "invalid_token", "error_description": "Token not found"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{issuer_url}/.well-known/oauth-protected-resource"'
                    },
                )

            if token_data["expires_at"] < datetime.utcnow():
                return JSONResponse(
                    {"error": "invalid_token", "error_description": "Token expired"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{issuer_url}/.well-known/oauth-protected-resource"'
                    },
                )

        response = await call_next(request)

        # Add headers to disable proxy buffering for SSE/streaming (critical for nginx/Synology)
        if request.url.path == "/":
            response.headers["X-Accel-Buffering"] = "no"
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

        return response


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Health check endpoint for container orchestration."""
    return PlainTextResponse("OK")


# =============================================================================
# OAuth 2.0 Endpoints
# =============================================================================


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request) -> JSONResponse:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    issuer_url = get_issuer_url(request)
    return JSONResponse(
        {
            "issuer": issuer_url,
            "authorization_endpoint": f"{issuer_url}/authorize",
            "token_endpoint": f"{issuer_url}/token",
            "registration_endpoint": f"{issuer_url}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
            "scopes_supported": ["mcp:tools"],
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
    )


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def protected_resource_metadata(request: Request) -> JSONResponse:
    """OAuth 2.0 Protected Resource Metadata (RFC 9728)."""
    issuer_url = get_issuer_url(request)
    return JSONResponse(
        {
            "resource": issuer_url,
            "authorization_servers": [issuer_url],
            "scopes_supported": ["mcp:tools"],
            "bearer_methods_supported": ["header"],
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
    )


@mcp.custom_route("/register", methods=["POST"])
async def register_client(request: Request) -> JSONResponse:
    """OAuth 2.0 Dynamic Client Registration (RFC 7591)."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    client_id = secrets.token_urlsafe(16)
    client_secret = secrets.token_urlsafe(32)

    # Build client info - only include non-null values (Claude Web fails on nulls)
    client_info = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_issued_at": int(datetime.utcnow().timestamp()),
        "grant_types": body.get("grant_types") or ["authorization_code", "refresh_token"],
        "response_types": body.get("response_types") or ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }

    # Only add optional fields if they have values (avoid nulls)
    if body.get("client_name"):
        client_info["client_name"] = body["client_name"]
    if body.get("redirect_uris"):
        client_info["redirect_uris"] = body["redirect_uris"]
    if body.get("scope"):
        client_info["scope"] = body["scope"]

    registered_clients[client_id] = client_info
    print(f"Registered new OAuth client: {client_id}")
    _save_tokens_to_disk()

    return JSONResponse(
        client_info,
        status_code=201,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
    )


@mcp.custom_route("/authorize", methods=["GET"])
async def authorize_get(request: Request) -> HTMLResponse:
    """Display OAuth authorization login form."""
    client_id = request.query_params.get("client_id", "")
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    code_challenge = request.query_params.get("code_challenge", "")
    code_challenge_method = request.query_params.get("code_challenge_method", "S256")
    scope = request.query_params.get("scope", "mcp:tools")

    # Validate client_id
    if client_id not in registered_clients:
        return HTMLResponse(
            "<h1>Error</h1><p>Unknown client_id. Please register first.</p>",
            status_code=400,
        )

    # Simple HTML login form
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize - Memos MCP</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 400px; margin: 100px auto; padding: 20px; }}
        h1 {{ color: #333; }}
        form {{ background: #f5f5f5; padding: 20px; border-radius: 8px; }}
        label {{ display: block; margin-bottom: 5px; font-weight: 500; }}
        input[type="password"] {{ width: 100%; padding: 10px; margin-bottom: 15px;
                                  border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }}
        button {{ background: #007bff; color: white; padding: 10px 20px; border: none;
                  border-radius: 4px; cursor: pointer; width: 100%; font-size: 16px; }}
        button:hover {{ background: #0056b3; }}
        .info {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
    </style>
</head>
<body>
    <h1>Memos MCP</h1>
    <p class="info">An application is requesting access to your Memos.</p>
    <form method="POST" action="/authorize">
        <input type="hidden" name="client_id" value="{client_id}">
        <input type="hidden" name="redirect_uri" value="{redirect_uri}">
        <input type="hidden" name="state" value="{state}">
        <input type="hidden" name="code_challenge" value="{code_challenge}">
        <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
        <input type="hidden" name="scope" value="{scope}">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required autofocus>
        <button type="submit">Authorize</button>
    </form>
</body>
</html>"""
    return HTMLResponse(html)


@mcp.custom_route("/authorize", methods=["POST"])
async def authorize_post(request: Request) -> RedirectResponse:
    """Validate password and issue authorization code."""
    form = await request.form()

    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    state = form.get("state", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "S256")
    scope = form.get("scope", "mcp:tools")
    password = form.get("password", "")

    # Validate password
    if not OAUTH_PASSWORD:
        return HTMLResponse(
            "<h1>Error</h1><p>OAUTH_PASSWORD not configured on server.</p>",
            status_code=500,
        )

    if not secrets.compare_digest(password, OAUTH_PASSWORD):
        return HTMLResponse(
            "<h1>Error</h1><p>Invalid password. Please try again.</p>",
            status_code=401,
        )

    # Generate authorization code
    auth_code = secrets.token_urlsafe(32)
    authorization_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "expires_at": datetime.utcnow() + timedelta(minutes=5),
    }

    # Redirect back to client with authorization code
    params = {"code": auth_code}
    if state:
        params["state"] = state

    redirect_url = f"{redirect_uri}?{urlencode(params)}"
    return RedirectResponse(redirect_url, status_code=302)


@mcp.custom_route("/token", methods=["POST"])
async def token_endpoint(request: Request) -> JSONResponse:
    """Exchange authorization code or refresh token for access token."""
    # Handle both JSON and form-urlencoded content types
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = {k: v for k, v in form.items()}

    grant_type = data.get("grant_type", "")

    if grant_type == "authorization_code":
        code = data.get("code", "")
        code_verifier = data.get("code_verifier", "")
        client_id = data.get("client_id", "")

        # Validate authorization code
        code_data = authorization_codes.get(code)
        if not code_data:
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid authorization code"}, status_code=400)

        if code_data["expires_at"] < datetime.utcnow():
            del authorization_codes[code]
            return JSONResponse({"error": "invalid_grant", "error_description": "Authorization code expired"}, status_code=400)

        if code_data["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant", "error_description": "Client ID mismatch"}, status_code=400)

        # Validate PKCE
        if code_data.get("code_challenge"):
            if not verify_pkce(code_verifier, code_data["code_challenge"]):
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        # Generate tokens
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)

        expires_at = datetime.utcnow() + timedelta(seconds=OAUTH_TOKEN_EXPIRY_SECONDS)
        access_tokens[access_token] = {
            "client_id": client_id,
            "expires_at": expires_at,
            "scope": code_data["scope"],
        }
        refresh_tokens[refresh_token] = {
            "client_id": client_id,
            "scope": code_data["scope"],
        }
        print(f"Created new access token for client {client_id} (expires: {expires_at.isoformat()})")
        _save_tokens_to_disk()

        # Delete used authorization code
        del authorization_codes[code]

        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": OAUTH_TOKEN_EXPIRY_SECONDS,
                "refresh_token": refresh_token,
                "scope": code_data["scope"],
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store",
            },
        )

    elif grant_type == "refresh_token":
        refresh_token = data.get("refresh_token", "")
        client_id = data.get("client_id", "")

        token_data = refresh_tokens.get(refresh_token)
        if not token_data:
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid refresh token"}, status_code=400)

        if token_data["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant", "error_description": "Client ID mismatch"}, status_code=400)

        # Generate new access token
        new_access_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(seconds=OAUTH_TOKEN_EXPIRY_SECONDS)
        access_tokens[new_access_token] = {
            "client_id": client_id,
            "expires_at": expires_at,
            "scope": token_data["scope"],
        }
        print(f"Refreshed access token for client {client_id} (expires: {expires_at.isoformat()})")
        _save_tokens_to_disk()

        return JSONResponse(
            {
                "access_token": new_access_token,
                "token_type": "Bearer",
                "expires_in": OAUTH_TOKEN_EXPIRY_SECONDS,
                "scope": token_data["scope"],
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store",
            },
        )

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# =============================================================================
# Memos Tools
# =============================================================================


@mcp.tool()
async def search_memos(
    query: Optional[str] = None,
    creator_id: Optional[int] = None,
    tag: Optional[str] = None,
    visibility: Optional[str] = None,
    limit: int = 10,
    offset: int = 0
) -> str:
    """
    Search for memos with optional filters.

    Args:
        query: Text to search for in memo content
        creator_id: Filter by creator user ID
        tag: Filter by tag name
        visibility: Filter by visibility (PUBLIC, PROTECTED, PRIVATE)
        limit: Maximum number of results to return (default: 10)
        offset: Number of results to skip (default: 0)

    Returns:
        JSON string containing the list of matching memos
    """
    # Build filter expression
    filters = []

    if creator_id is not None:
        filters.append(f"creator_id == {creator_id}")

    if query:
        # Escape quotes in query
        escaped_query = query.replace('"', '\\"')
        filters.append(f'content.contains("{escaped_query}")')

    if tag:
        escaped_tag = tag.replace('"', '\\"')
        filters.append(f'tag in ["{escaped_tag}"]')

    if visibility:
        filters.append(f'visibility == "{visibility.upper()}"')

    # Combine filters with AND operator
    filter_str = " && ".join(filters) if filters else ""

    # Build request parameters
    params = {
        "pageSize": limit,
    }

    if filter_str:
        params["filter"] = filter_str

    # Calculate page token for pagination
    if offset > 0:
        # For simplicity, we'll use offset/limit approach
        # In production, you'd want to use proper page tokens
        page = offset // limit
        if page > 0:
            params["pageToken"] = f"offset={offset}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{MEMOS_BASE_URL}/api/v1/memos",
                params=params,
                headers=get_headers(),
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            # Format the response nicely
            memos = data.get("memos", [])
            result = {
                "count": len(memos),
                "memos": [
                    {
                        "name": memo.get("name"),
                        "uid": memo.get("uid"),
                        "creator": memo.get("creator"),
                        "content": memo.get("content"),
                        "visibility": memo.get("visibility"),
                        "pinned": memo.get("pinned", False),
                        "createTime": memo.get("createTime"),
                        "updateTime": memo.get("updateTime"),
                        "displayTime": memo.get("displayTime"),
                    }
                    for memo in memos
                ],
                "nextPageToken": data.get("nextPageToken", "")
            }

            return str(result)

    except httpx.HTTPError as e:
        return f"Error searching memos: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@mcp.tool()
async def create_memo(
    content: str,
    visibility: str = "PRIVATE"
) -> str:
    """
    Create a new memo.

    Args:
        content: The content of the memo (supports Markdown)
        visibility: Visibility level - PUBLIC, PROTECTED, or PRIVATE (default: PRIVATE)

    Returns:
        JSON string containing the created memo details
    """
    # Validate visibility
    valid_visibilities = ["PUBLIC", "PROTECTED", "PRIVATE"]
    visibility = visibility.upper()
    if visibility not in valid_visibilities:
        return f"Error: visibility must be one of {', '.join(valid_visibilities)}"

    # Build request payload
    payload = {
        "content": content,
        "visibility": visibility
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MEMOS_BASE_URL}/api/v1/memos",
                json=payload,
                headers=get_headers(),
                timeout=30.0
            )
            response.raise_for_status()
            memo = response.json()

            # Format the response
            result = {
                "success": True,
                "memo": {
                    "name": memo.get("name"),
                    "uid": memo.get("uid"),
                    "creator": memo.get("creator"),
                    "content": memo.get("content"),
                    "visibility": memo.get("visibility"),
                    "pinned": memo.get("pinned", False),
                    "createTime": memo.get("createTime"),
                    "updateTime": memo.get("updateTime"),
                    "displayTime": memo.get("displayTime"),
                }
            }

            return str(result)

    except httpx.HTTPError as e:
        return f"Error creating memo: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@mcp.tool()
async def update_memo(
    memo_uid: str,
    content: Optional[str] = None,
    visibility: Optional[str] = None,
    pinned: Optional[bool] = None
) -> str:
    """
    Update an existing memo.

    Args:
        memo_uid: The UID of the memo to update (e.g., "abc123")
        content: New content for the memo (optional)
        visibility: New visibility level - PUBLIC, PROTECTED, or PRIVATE (optional)
        pinned: Whether to pin the memo (optional)

    Returns:
        JSON string containing the updated memo details
    """
    # Validate visibility if provided
    if visibility is not None:
        valid_visibilities = ["PUBLIC", "PROTECTED", "PRIVATE"]
        visibility = visibility.upper()
        if visibility not in valid_visibilities:
            return f"Error: visibility must be one of {', '.join(valid_visibilities)}"

    # Build update payload and update mask
    memo_data = {"state": "STATE_UNSPECIFIED"}
    update_paths = []

    if content is not None:
        memo_data["content"] = content
        update_paths.append("content")

    if visibility is not None:
        memo_data["visibility"] = visibility
        update_paths.append("visibility")

    if pinned is not None:
        memo_data["pinned"] = pinned
        update_paths.append("pinned")

    if not update_paths:
        return "Error: At least one field (content, visibility, or pinned) must be provided for update"

    # Build the full payload
    memo_name = f"memos/{memo_uid}"
    payload = {
        **memo_data
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{MEMOS_BASE_URL}/api/v1/{memo_name}",
                json=payload,
                headers=get_headers(),
                timeout=30.0
            )
            response.raise_for_status()
            memo = response.json()

            # Format the response
            result = {
                "success": True,
                "memo": {
                    "name": memo.get("name"),
                    "uid": memo.get("uid"),
                    "creator": memo.get("creator"),
                    "content": memo.get("content"),
                    "visibility": memo.get("visibility"),
                    "pinned": memo.get("pinned", False),
                    "createTime": memo.get("createTime"),
                    "updateTime": memo.get("updateTime"),
                    "displayTime": memo.get("displayTime"),
                }
            }

            return str(result)

    except httpx.HTTPError as e:
        return f"Error updating memo: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@mcp.tool()
async def get_memo(memo_uid: str) -> str:
    """
    Get a specific memo by its UID.

    Args:
        memo_uid: The UID of the memo to retrieve (e.g., "abc123")

    Returns:
        JSON string containing the memo details
    """
    memo_name = f"memos/{memo_uid}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{MEMOS_BASE_URL}/api/v1/{memo_name}",
                headers=get_headers(),
                timeout=30.0
            )
            response.raise_for_status()
            memo = response.json()

            # Format the response
            result = {
                "name": memo.get("name"),
                "uid": memo.get("uid"),
                "creator": memo.get("creator"),
                "content": memo.get("content"),
                "visibility": memo.get("visibility"),
                "pinned": memo.get("pinned", False),
                "createTime": memo.get("createTime"),
                "updateTime": memo.get("updateTime"),
                "displayTime": memo.get("displayTime"),
                "snippet": memo.get("snippet", ""),
            }

            return str(result)

    except httpx.HTTPError as e:
        return f"Error getting memo: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


def create_app():
    """Create ASGI app with OAuth authentication and CORS middleware."""
    return mcp.http_app(
        path="/",
        transport="streamable-http",
        middleware=[
            # CORS middleware MUST come first to handle preflight OPTIONS requests
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id"],
            ),
            Middleware(OAuthAuthMiddleware),
        ],
        stateless_http=True,  # Required for Claude.ai remote MCP connections
        json_response=True,   # Use JSON responses instead of SSE (better proxy compatibility)
    )


# ASGI application for uvicorn
app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("MEMOS_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MEMOS_MCP_PORT", "8716"))
    uvicorn.run("server:app", host=host, port=port)
