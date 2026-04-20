Author: Ahmed Sadaqat

# Setup
```bash
cd /home/dev/development/combioml-test

export ANTHROPIC_API_KEY="YOUR_KEY_HERE"

sudo docker compose down --remove-orphans
sudo docker compose up --build
```

```bash
# Frontend
http://localhost:3000

# Backend
http://localhost:8080

# noVNC (per session)
http://localhost:6080/vnc.html
```

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Browser
    participant Nginx as Frontend (nginx :3000)
    participant API as Backend (FastAPI :8080)
    participant SM as SessionManager
    participant DB as PostgreSQL
    participant VNC as Xvfb / mutter / x11vnc / noVNC
    participant Runner as runner.py (subprocess)
    participant Claude as Anthropic API

    Note over User,Claude: 1) Load UI
    User->>Browser: Open app
    Browser->>Nginx: GET /
    Nginx-->>Browser: index.html + app.js
    Browser->>API: GET /sessions (optional)

    Note over User,Claude: 2) New session
    User->>Browser: New Session
    Browser->>API: POST /sessions
    API->>SM: start_session_infra(session_id)
    SM->>VNC: Start Xvfb, WM, x11vnc, novnc_proxy
    VNC-->>SM: display_num, novnc_port, vnc_port
    API->>DB: INSERT AgentSession
    DB-->>API: ok
    API-->>Browser: { session_id, display_num, novnc_port, ... }

    Note over User,Claude: 3) Live desktop (parallel to chat)
    Browser->>VNC: iframe GET https?://host:novnc_port/vnc.html?...
    Note right of VNC: Traffic goes to published host ports<br/>(6080–6089), not through FastAPI

    Note over User,Claude: 4) Select session + history + SSE
    Browser->>API: GET /sessions/{id}/messages
    API->>DB: SELECT messages
    DB-->>API: rows
    API-->>Browser: JSON history
    Browser->>API: EventSource GET /sessions/{id}/stream
    API-->>Browser: SSE connected + heartbeats

    Note over User,Claude: 5) Send task
    User->>Browser: Submit message
    Browser->>API: POST /sessions/{id}/message
    API->>DB: INSERT user AgentMessage
    API->>API: asyncio.create_task(_run_agent)
    API-->>Browser: 200 { status: ok }

    API->>Runner: spawn python -m computer_use_demo.runner
    Runner->>DB: load conversation
    Runner->>Claude: messages + tools (computer, bash, …)
    Claude-->>Runner: tool_use / text
    loop Tool calls
        Runner->>VNC: xdotool / GUI / bash on DISPLAY
        Runner-->>API: JSON lines on stdout/stderr
        API->>API: queue.put(line)
        API-->>Browser: SSE data events
    end
    Runner-->>API: process exit
    API->>API: queue.put(finished status); queue.put(None)
    API-->>Browser: SSE (status / ping)

```