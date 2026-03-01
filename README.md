# TokioAI

**AI Agent Framework** — A modular, self-hosted AI agent that executes real actions on your infrastructure via CLI, API, and Telegram.

TokioAI connects an LLM (Claude, GPT, Gemini) to your servers, databases, Docker containers, IoT devices, DNS, and cloud infrastructure through a secure tool-calling architecture. It's not a chatbot — it's an autonomous agent that gets things done.

```
You: "restart the nginx container and show me the last 20 lines of its logs"
TokioAI: [executes docker restart nginx, docker logs --tail 20 nginx, returns results]
```

## Features

### Multi-Provider LLM Support
- **Anthropic Claude** (direct API or Vertex AI) — recommended
- **OpenAI GPT** (GPT-4o, GPT-4, etc.)
- **Google Gemini** (Gemini 2.0 Flash, Pro)
- Automatic fallback between providers

### Three Interfaces
- **CLI** — Interactive terminal with Rich formatting
- **REST API** — FastAPI server with auth, rate limiting, CORS
- **Telegram Bot** — Full multimedia support:
  - Send/receive images (analyzed via Vision API)
  - Voice messages (transcribed via Whisper/Gemini)
  - Audio files
  - Documents (PDF, DOCX, CSV, code files)
  - YouTube link analysis
  - File generation and sending (PDF, CSV, PPTX)

### 30+ Built-in Tools
| Category | Tools |
|----------|-------|
| **System** | `bash`, `python`, `read_file`, `write_file` |
| **Network** | `curl`, `wget` |
| **Docker** | `ps`, `logs`, `start`, `stop`, `restart`, `exec`, `stats`, `inspect` |
| **Database** | `postgres_query` (read/write with SQL injection protection) |
| **SSH** | `host_control` (remote server management via SSH) |
| **DNS** | `hostinger` (DNS record management) |
| **IoT** | `home_assistant` (lights, switches, sensors, automations) |
| **Cloud** | `gcp_waf` (deploy/manage WAF on GCP) |
| **Router** | `router` (OpenWrt/SSH router management) |
| **Tunnels** | `cloudflared` (Cloudflare tunnel management) |
| **Security** | `prompt_guard` (LLM prompt injection detection + audit log) |
| **Documents** | `document` (generate PDF, PPTX, CSV, Markdown) |
| **Tasks** | `task_orchestrator` (multi-step task automation) |
| **Calendar** | `calendar` (Google Calendar integration) |

### Security
- **Prompt Guard** — WAF for LLM prompts that detects and blocks injection attacks (role override, system prompt extraction, delimiter injection, encoding attacks, tool abuse). All attempts logged to PostgreSQL.
- **Input Sanitizer** — Blocks reverse shells, crypto miners, fork bombs, destructive commands, SQL injection, and path traversal before tool execution.
- **Secure Channel** — API key authentication + rate limiting for the REST API.
- **Telegram ACL** — Owner-based access control with allow/deny lists.

### Agent Engine
- Multi-round tool-calling loop with automatic retry
- Session memory (conversation history persisted to PostgreSQL)
- Workspace memory (persistent notes across sessions)
- Error learning (remembers past failures to avoid repeating them)
- Context builder (dynamic system prompts based on available tools)
- Container watchdog (auto-restarts crashed containers)
- Plugin system for custom tools

### WAF Module (Optional)
Includes a full WAF (Web Application Firewall) deployment in `tokio_cloud/`:
- Nginx reverse proxy with rate limiting and honeypots
- Real-time attack detection via Kafka
- 26 WAF signatures + 7 behavioral rules
- IP reputation scoring
- Multi-phase attack correlation
- SOC dashboard with live attack feed, world map, and heatmaps
- GeoIP integration

## Quick Start

### Option 1: Setup Wizard (recommended)

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
pip install -e .
tokio setup
```

The wizard walks you through configuring your LLM provider, database, and optional features, then generates `.env` and `docker-compose.yml`.

### Option 2: Manual Setup

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai

# Configure
cp .env.example .env
# Edit .env with your API keys and settings

# Install
pip install -e .

# Run CLI
tokio

# Or run API server
tokio server
```

### Option 3: Docker

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
cp .env.example .env
# Edit .env

docker compose up -d
```

This starts:
- `tokio-cli` — API server on port 8200
- `tokio-telegram` — Telegram bot (if configured)

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in your values.

### Required
| Variable | Description |
|----------|-------------|
| `LLM_PROVIDER` | `anthropic`, `openai`, or `gemini` |
| `ANTHROPIC_API_KEY` | Claude API key (or use Vertex AI) |
| `POSTGRES_PASSWORD` | PostgreSQL password |

### LLM via Vertex AI (for Claude Opus)
| Variable | Description |
|----------|-------------|
| `USE_ANTHROPIC_VERTEX` | `true` to use Vertex AI |
| `GCP_PROJECT_ID` | Your GCP project |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON |
| `ANTHROPIC_VERTEX_REGION` | Region (e.g., `us-east5`) |

### Optional Features
| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_OWNER_ID` | Your Telegram user ID (use `/myid`) |
| `HOST_SSH_HOST` | Remote server for SSH control |
| `HOME_ASSISTANT_URL` | Home Assistant instance URL |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token |
| `HOSTINGER_API_TOKEN` | Hostinger DNS API token |

See `.env.example` for the full list.

## Architecture

```
                    ┌─────────────┐
                    │   Telegram   │
                    │     Bot      │
                    └──────┬──────┘
                           │
┌─────────┐         ┌──────┴──────┐         ┌──────────────┐
│   CLI   │────────>│  FastAPI    │────────>│  Agent Loop   │
└─────────┘         │  Server    │         │  (multi-round) │
                    └─────────────┘         └──────┬───────┘
                                                    │
                                            ┌───────┴───────┐
                                            │  Tool Executor │
                                            │  (timeouts,    │
                                            │   circuit      │
                                            │   breaker)     │
                                            └───────┬───────┘
                                                    │
                    ┌───────────────────────────────┼────────────────┐
                    │               │               │                │
              ┌─────┴─────┐  ┌─────┴─────┐  ┌─────┴─────┐  ┌──────┴──────┐
              │   bash    │  │  docker   │  │ postgres  │  │  host_ssh   │
              │  python   │  │  logs     │  │  query    │  │  curl/wget  │
              │  files    │  │  restart  │  │           │  │  IoT / DNS  │
              └───────────┘  └───────────┘  └───────────┘  └─────────────┘
```

### Key Modules
| Module | Description | Lines |
|--------|-------------|-------|
| `engine/agent.py` | Multi-round agent loop with tool calling | 462 |
| `engine/tools/registry.py` | Tool registration and discovery | 116 |
| `engine/tools/executor.py` | Async execution with timeouts and circuit breaker | 210 |
| `engine/tools/builtin/loader.py` | Registers all 30+ built-in tools | 542 |
| `engine/security/prompt_guard.py` | Prompt injection WAF | 223 |
| `engine/security/input_sanitizer.py` | Command/SQL/path sanitization | 161 |
| `engine/memory/session.py` | Conversation persistence | 152 |
| `engine/memory/workspace.py` | Cross-session persistent memory | 283 |
| `engine/context_builder.py` | Dynamic system prompt builder | 153 |
| `engine/error_learner.py` | Learn from past tool failures | 143 |
| `engine/watchdog.py` | Container health monitoring + auto-restart | 351 |
| `engine/llm/` | Multi-provider LLM abstraction | 6 files |
| `cli.py` | Interactive CLI with Rich | 181 |
| `api/server.py` | FastAPI REST server | 269 |
| `bots/telegram_bot.py` | Full multimedia Telegram bot | 1127 |
| `setup_wizard.py` | Interactive setup wizard | 707 |

## Adding Custom Tools

### Built-in Tool
Create a file in `tokio_agent/engine/tools/builtin/`:

```python
# my_tools.py
import logging

logger = logging.getLogger(__name__)

async def my_custom_tool(action: str, params: dict = None) -> str:
    """Your custom tool logic."""
    params = params or {}
    if action == "hello":
        return f"Hello, {params.get('name', 'world')}!"
    return f"Unknown action: {action}"
```

Register in `loader.py`:

```python
from .my_tools import my_custom_tool

registry.register(
    name="my_tool",
    description="My custom tool",
    category="Custom",
    parameters={"action": "Action to perform", "params": "Additional parameters"},
    executor=my_custom_tool,
)
```

### Plugin Tool
Drop a Python file in `workspace/plugins/`:

```python
# workspace/plugins/weather.py
TOOL_NAME = "weather"
TOOL_DESCRIPTION = "Get current weather for a city"
TOOL_PARAMETERS = {"city": "City name"}
TOOL_CATEGORY = "Custom"

async def execute(city: str) -> str:
    import httpx
    resp = await httpx.AsyncClient().get(f"https://wttr.in/{city}?format=3")
    return resp.text
```

Plugins are auto-discovered on startup.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Send a message and get a response |
| `GET` | `/health` | Health check |
| `GET` | `/tools` | List available tools |
| `GET` | `/sessions` | List sessions |
| `DELETE` | `/sessions/{id}` | Delete a session |

### Example API Call

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"message": "list running docker containers", "session_id": "my-session"}'
```

## WAF Deployment (Optional)

The `tokio_cloud/` directory contains a complete WAF deployment:

```bash
cd tokio_cloud/gcp-live
cp .env.example .env
# Edit .env with your passwords
docker compose up -d
```

This deploys 7 containers: PostgreSQL, Zookeeper, Kafka, Nginx WAF proxy, log processor, realtime attack detector, and SOC dashboard.

See `tokio_cloud/waf-deployment/README.md` for detailed instructions.

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Project Structure

```
tokioai/
├── tokio_agent/
│   ├── cli.py                    # Interactive CLI
│   ├── setup_wizard.py           # Setup wizard (tokio setup)
│   ├── api/
│   │   └── server.py             # FastAPI REST server
│   ├── bots/
│   │   ├── telegram_bot.py       # Telegram bot
│   │   └── Dockerfile.telegram
│   └── engine/
│       ├── agent.py              # Agent loop
│       ├── context_builder.py    # System prompt builder
│       ├── db.py                 # PostgreSQL helpers
│       ├── error_learner.py      # Error learning
│       ├── watchdog.py           # Container watchdog
│       ├── llm/                  # LLM providers
│       │   ├── anthropic_llm.py
│       │   ├── openai_llm.py
│       │   └── gemini_llm.py
│       ├── memory/               # Persistence
│       │   ├── session.py
│       │   └── workspace.py
│       ├── security/             # Security layers
│       │   ├── prompt_guard.py
│       │   ├── input_sanitizer.py
│       │   └── secure_channel.py
│       └── tools/
│           ├── registry.py
│           ├── executor.py
│           ├── plugins/          # Plugin loader
│           └── builtin/          # 30+ built-in tools
├── tokio_cloud/                  # WAF deployment (optional)
│   ├── gcp-live/                 # Production WAF config
│   └── waf-deployment/           # WAF setup docs
├── tests/                        # Test suite
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## Requirements

- Python 3.11+
- PostgreSQL 15+ (for session/memory persistence)
- Docker (optional, for containerized deployment)
- At least one LLM API key (Anthropic, OpenAI, or Gemini)

## License

GPL v3 — See [LICENSE](LICENSE) for details.
