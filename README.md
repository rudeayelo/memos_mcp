# Memos MCP Server

A remote MCP (Model Context Protocol) server that provides tools for interacting with a [Memos](https://github.com/usememos/memos) instance. This server allows AI assistants to search, create, and update memos through the Memos API.

Uses **Streamable HTTP transport** (MCP spec 2025-03-26) for remote deployment with **OAuth 2.0** authentication.

## Features

- **Search Memos**: Search for memos with filters like creator, tags, visibility, and content
- **Create Memos**: Create new memos with markdown support
- **Update Memos**: Update existing memos (content, visibility, pinned status)
- **Get Memo**: Retrieve a specific memo by UID
- **Remote Deployment**: Run as a Docker container accessible over HTTP
- **OAuth 2.0 Authentication**: Works with Claude Desktop, Claude.ai, and mobile apps

## Quick Start (Docker)

1. Clone and configure:
```bash
git clone <repository-url>
cd memos_mcp
cp .env.example .env
```

2. Edit `.env` with your settings:
```bash
# Required: Set your Memos instance URL and token
MEMOS_BASE_URL=http://your-memos-instance:5230
MEMOS_API_TOKEN=your-memos-token

# Required: OAuth settings
OAUTH_PASSWORD=your-secure-password
OAUTH_ISSUER_URL=https://your-public-server-url.com
```

3. Build and run:
```bash
docker compose up -d
```

4. Verify:
```bash
curl http://localhost:8716/health
```

## Installation (Development)

1. Clone this repository:
```bash
git clone <repository-url>
cd memos_mcp
```

2. Install dependencies:

### Using uv (recommended)
```bash
uv sync
```

### Using pip
```bash
pip install -r requirements.txt
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MEMOS_BASE_URL` | URL of your Memos instance | `http://localhost:5230` |
| `MEMOS_API_TOKEN` | API token for Memos authentication | (none) |
| `OAUTH_PASSWORD` | Password for OAuth authorization | (required) |
| `OAUTH_ISSUER_URL` | Public HTTPS URL of this server | `http://localhost:8716` |
| `OAUTH_TOKEN_EXPIRY_SECONDS` | Access token lifetime | `3600` |

### Getting a Memos API Token

1. Log into your Memos instance
2. Go to Settings → Access Tokens
3. Create a new access token
4. Copy the token and set it as `MEMOS_API_TOKEN`

## Usage

### Running the Server

#### Docker (recommended for production)
```bash
docker compose up -d
```

#### Direct with uvicorn (development)
```bash
uv sync
uv run uvicorn server:app --host 0.0.0.0 --port 8716
```

#### Python directly
```bash
python server.py
```

### Available Tools

#### 1. search_memos
Search for memos with optional filters.

**Parameters:**
- `query` (optional): Text to search for in memo content
- `creator_id` (optional): Filter by creator user ID
- `tag` (optional): Filter by tag name
- `visibility` (optional): Filter by visibility (PUBLIC, PROTECTED, PRIVATE)
- `limit` (default: 10): Maximum number of results
- `offset` (default: 0): Number of results to skip

**Example:**
```python
result = await search_memos(query="meeting notes", limit=5)
```

#### 2. create_memo
Create a new memo.

**Parameters:**
- `content`: The content of the memo (supports Markdown)
- `visibility` (default: PRIVATE): Visibility level (PUBLIC, PROTECTED, PRIVATE)

**Example:**
```python
result = await create_memo(
    content="# Meeting Notes\n\n- Discuss project timeline\n- Review budget",
    visibility="PRIVATE"
)
```

#### 3. update_memo
Update an existing memo.

**Parameters:**
- `memo_uid`: The UID of the memo to update
- `content` (optional): New content for the memo
- `visibility` (optional): New visibility level
- `pinned` (optional): Whether to pin the memo

**Example:**
```python
result = await update_memo(
    memo_uid="abc123",
    content="Updated content",
    pinned=True
)
```

#### 4. get_memo
Get a specific memo by its UID.

**Parameters:**
- `memo_uid`: The UID of the memo to retrieve

**Example:**
```python
result = await get_memo(memo_uid="abc123")
```

## Integration with Claude

This server uses **OAuth 2.0** authentication, making it compatible with Claude as a Remote MCP connector.

### Adding as a Claude Connector

1. Go to Claude Settings → Connectors → Add Connector
2. Enter your server URL: `https://your-server.example.com`
3. Claude will discover the OAuth endpoints automatically
4. You'll be redirected to a login page - enter your `OAUTH_PASSWORD`
5. Once authorized, Claude can access your Memos

### How OAuth Works

When you connect Claude to this server:

1. Claude discovers OAuth settings via `/.well-known/oauth-authorization-server`
2. Claude registers itself as an OAuth client via `/register`
3. You're shown a login page at `/authorize` - enter your password
4. Claude receives an access token and uses it for MCP requests
5. Tokens expire after `OAUTH_TOKEN_EXPIRY_SECONDS` (default: 1 hour)
6. Claude automatically refreshes tokens when needed

### OAuth Endpoints

| Endpoint | Description |
|----------|-------------|
| `/.well-known/oauth-authorization-server` | OAuth metadata discovery |
| `/.well-known/oauth-protected-resource` | Protected resource metadata |
| `/register` | Dynamic client registration |
| `/authorize` | Authorization (login) page |
| `/token` | Token exchange endpoint |

### Security Notes

- **HTTPS Required**: OAuth tokens must be transmitted over HTTPS
- **In-Memory Tokens**: Tokens are stored in memory and lost on server restart (you'll need to re-authorize)
- **Single User**: This implementation uses a single password for all access
- **PKCE**: Full PKCE support for secure token exchange

## Architecture

```
┌─────────────────┐     HTTPS      ┌─────────────────┐     HTTP      ┌─────────────────┐
│   Claude        │ ──────────────>│  Memos MCP      │ ────────────>│    Memos        │
│ (Desktop/Web)   │  OAuth 2.0     │  Server         │   API Token  │    Instance     │
└─────────────────┘                └─────────────────┘              └─────────────────┘
```

**OAuth Flow:**
```
1. Claude ──> GET /.well-known/oauth-authorization-server (discover endpoints)
2. Claude ──> POST /register (register as client)
3. Claude ──> GET /authorize?... (redirect user to login)
4. User enters password, POST /authorize
5. Claude ──> POST /token (exchange code for access token)
6. Claude ──> POST /mcp (use access token for MCP requests)
```

## API Reference

This server is built on the Memos API v1. The API follows Google's API Improvement Proposals (AIPs) design guidelines.

### API Endpoints Used

- `GET /api/v1/memos` - List/search memos
- `POST /api/v1/memos` - Create a memo
- `GET /api/v1/memos/{uid}` - Get a specific memo
- `PATCH /api/v1/memos/{uid}` - Update a memo

## Development

### Running Tests

```bash
pytest
```

### Code Structure

- `server.py`: Main MCP server implementation with OAuth and tools
- `requirements.txt`: Python dependencies

## About Memos

Memos is a lightweight, self-hosted memo hub with knowledge management and social networking features. Learn more at:
- Website: https://www.usememos.com/
- GitHub: https://github.com/usememos/memos

## License

MIT License - see LICENSE file for details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
