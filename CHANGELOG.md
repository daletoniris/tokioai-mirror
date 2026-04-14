# Changelog

All notable changes to TokioAI are documented here.

## [4.0.0] — 2026-04-14

### 🖥️ CLI v4.0 — Pro Terminal Interface (1963 lines)

**Terminal Stability (Zero Keyboard Hangs)**
- 3-layer terminal restore: golden state capture → `stty sane` → show cursor
- `_drain_stdin()` — clears keyboard buffer after every tool execution
- `atexit` + signal handlers (SIGINT/SIGTERM/SIGHUP) guarantee cleanup
- Escape key fix: `os.read()` instead of `sys.stdin.read()` (no more blocking)

**Unlimited Mode by Default**
- `max_rounds = 0` (unlimited) — no more "Maximo de iteraciones alcanzado"
- `max_time = 0` (no timeout) — agent works until task is complete
- Agent engine: `MAX_TOOL_ROUNDS = 0`, `MAX_TOTAL_TIME = 0`

**15 Slash Commands** (respond instantly, no LLM needed)
- `/status` — Entity + GCP + PiCar overview
- `/entity` — Entity status (camera, Hailo, FPS)
- `/picar` — PiCar-X robot status
- `/health` — Health data with color coding (red if HR>120, SpO2<92)
- `/gcp` — GCP containers status
- `/waf` — WAF attack stats (JWT auth)
- `/wifi` — WiFi defense status
- `/coffee` — Coffee machine status
- `/ha` — Home Assistant status
- `/see` — AI vision analysis
- `/logs` — Entity logs with colors
- `/sitrep` — Full situation report
- `/threats` — Threat assessment
- `/drone` — Drone status
- `/containers` — Docker container list

**Cost Tracking**
- Per-response token cost estimation (input + output)
- Accumulated session costs persisted to `~/.tokio_cli_costs.json`
- Supports Claude Opus, Sonnet, and Haiku pricing

**Other Improvements**
- Tab completion for commands, slash commands, and tool names
- Multi-line input (end line with `\`)
- Terminal recovery with `stty sane` if anything goes wrong

---

### 🏥 Health Monitoring System — Quantified Self & Biohacking

**Philosophy**
- Quantified self / biohacking approach to personal health optimization
- Continuous self-measurement, data-driven health decisions
- Real results: cholesterol 250→103 mg/dL (-58.8%) without statins

**Accu-Answer iSaw 4-in-1 Integration**
- AI-powered OCR: send photo of iSaw device via Telegram → auto-read and store
- Supported metrics: glucose, cholesterol, hemoglobin, uric acid
- Values displayed on Entity's live panel (LAB section)
- Custom ranges for Accu-Answer (hemoglobin: 11.0-16.0 g/dL)

**Health Database**
- SQLite storage on Raspberry Pi (`health_db.py`)
- 1,900+ readings stored across 24 days
- API endpoints: `/health/db/latest`, `/health/db/store`, `/health/db/history`
- 7 tracked metrics: HR, BP, SpO2, glucose, cholesterol, hemoglobin, uric acid

**Agent Tools**
- `health_store` — Store lab readings from any interface
- `health_full` — Combined report (smartwatch + lab values)
- `health` — Full multi-day history with assessment

**AI Health Analysis**
- Automatic trend detection and scoring (0-10 per metric)
- Cross-metric correlation (e.g., cholesterol improvement + HR improvement)
- Historical comparison with percentage changes

---

### 🤖 PiCar-X Robot Integration

**Safety Proxy** (`picar_proxy.py` — 778 lines)
- REST API on port 5002 (Raspberry Pi 5)
- Speed limiting (max 80), duration limits (max 10s)
- Emergency kill switch
- Rate limiting and command audit log
- Systemd service with auto-restart

**Agent Tools** (`picar_tools.py` — 277 lines)
- 36 registered tools for full robot control
- Natural language: "move forward 30cm", "avoid obstacles", "dance"
- Sensor readings: ultrasonic distance, grayscale line detection
- Camera control: pan/tilt, snapshot
- Autonomous modes: obstacle avoidance, line tracking, patrol patterns

**Network Access**
- LAN: `<local-ip>:5002`
- Tailscale: `<tailscale-ip>:5002`

---

### 🛡️ Entity Hardening

**Singleton Protection**
- `flock` + lock file prevents dual-instance (was causing camera conflicts)
- `ExecStartPre` kills port 5000, `ExecStopPost` cleans lock file
- Graceful shutdown with SIGTERM/SIGINT handlers

**Self-Healing Improvements**
- Uses `systemctl restart tokio-entity` instead of fragile `pkill + nohup` scripts
- Auto-detects and kills zombie processes
- HA Docker auto-restart if container goes down

**Camera Stability**
- `try/finally` with `vision.stop()` ensures camera release on crash
- `__del__` safety net in VisionEngine
- No more "camera busy" errors from zombie processes

**SecurityFeed**
- Auto-disables if WAF is not configured (no more error spam in logs)

---

### 🤖 Drone Improvements

**Error Messages**
- Actionable errors: "Proxy down — check Raspi", "WiFi not connected to drone"
- Instead of raw httpx exceptions

**Variable Timeouts**
- `takeoff`: 20s, `patrol`: 30s, `move`: 15s, `land`: 15s
- Instead of fixed 10s for everything

---

### 📱 Telegram Bot

**Medical Image Processing**
- Auto-detects Accu-Answer iSaw photos
- Extracts metric values via Gemini Vision OCR
- Stores readings in health database
- Confirms with summary table

**Quick Commands**
- 7 instant commands: `/status`, `/sitrep`, `/waf`, `/health`, `/threats`, `/drone`, `/entity`
- WAF command uses JWT authentication

---

### 🏗️ Infrastructure

**GCP Agent**
- 37 registered tools (added: `picar`, `health_store`, `health_full`)
- Unlimited tool rounds and time by default
- Context builder with smart memory injection

**Sitrep**
- Updated with PiCar-X status and Tailscale IP
- Health DB stats included
- Entity singleton status

---

## [3.3.0] — 2026-04-13

- PiCar-X proxy initial deployment
- Health DB endpoints (`/health/db/latest`, `/health/db/store`)
- CLI v3.3 with basic slash commands
- Entity display panels for health data

## [3.2.0] — 2026-04-13

- CLI token cost tracking
- WiFi defense slash command
- Coffee machine slash command
- Home Assistant slash command
- Tab completion for 25 commands

## [3.1.0] — 2026-04-13

- 9 slash commands (respond without LLM)
- Telegram quick commands with JWT auth for WAF
- CLI entry points: `tokioai` and `tokio-ops`

## [3.0.0] — 2026-04-06

- Native tool use (structured JSON via LLM API)
- 5 LLM providers: Anthropic, OpenAI, Google, OpenRouter, Ollama
- Streaming CLI with escape-to-cancel
- Auto-compaction at 80% context capacity
- Subagent workers for parallel tasks
- Self-healing engine
- 150+ tests

