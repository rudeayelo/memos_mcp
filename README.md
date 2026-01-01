# Memos MCP Server

A remote MCP (Model Context Protocol) server that provides tools for interacting with a [Memos](https://github.com/usememos/memos) instance. This server allows AI assistants to search, create, and update memos through the Memos API.

Uses **Streamable HTTP transport** (MCP spec 2025-03-26) for remote deployment.

## Features

- **Search Memos**: Search for memos with filters like creator, tags, visibility, and content
- **Create Memos**: Create new memos with markdown support
- **Update Memos**: Update existing memos (content, visibility, pinned status)
- **Get Memo**: Retrieve a specific memo by UID
- **Remote Deployment**: Run as a Docker container accessible over HTTP
- **API Key Authentication**: Secure access to the MCP server

## Quick Start (Docker)

1. Clone and configure:
```bash
git clone <repository-url>
cd memos_mcp
cp .env.example .env
```

2. Generate an API key and edit `.env`:
```bash
openssl rand -base64 32
# Add the generated key to MEMOS_MCP_API_KEY in .env
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
| `MEMOS_MCP_API_KEY` | API key for incoming MCP client auth | (required) |

### Getting a Memos API Token

1. Log into your Memos instance
2. Go to Settings → Access Tokens
3. Create a new access token
4. Copy the token and set it as `MEMOS_API_TOKEN`

### Generating an MCP API Key

```bash
openssl rand -base64 32
```

Copy the output and set it as `MEMOS_MCP_API_KEY`.

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

## Integration with MCP Clients

This server uses **Streamable HTTP transport**, allowing remote connections from any MCP-compatible client.

### Claude Desktop

Add to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "memos": {
      "transport": "streamable-http",
      "url": "https://your-server.example.com/mcp",
      "headers": {
        "Authorization": "Bearer your-memos-mcp-api-key"
      }
    }
  }
}
```

### Other MCP Clients

Configure your client with:
- **Transport**: `streamable-http`
- **URL**: `http://your-server:8716/mcp`
- **Header**: `Authorization: Bearer <MEMOS_MCP_API_KEY>`

### Security Notes

For internet-facing deployments:
- Use a reverse proxy (nginx, Traefik, Caddy) with TLS
- The `/health` endpoint is public (no auth required) for container orchestration
- The `/mcp` endpoint requires Bearer token authentication

## Architecture

```
┌─────────────────┐     HTTPS      ┌─────────────────┐     HTTP      ┌─────────────────┐
│   MCP Client    │ ──────────────>│  Memos MCP      │ ────────────>│    Memos        │
│  (AI Assistant) │  Bearer Token  │  Server (HTTP)  │   API Token  │    Instance     │
└─────────────────┘                └─────────────────┘              └─────────────────┘
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

- `server.py`: Main MCP server implementation with all tools
- `requirements.txt`: Python dependencies

## About Memos

Memos is a lightweight, self-hosted memo hub with knowledge management and social networking features. Learn more at:
- Website: https://www.usememos.com/
- GitHub: https://github.com/usememos/memos

## License

MIT License - see LICENSE file for details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
