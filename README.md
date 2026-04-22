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
# Each session starts its own desktop container; the backend returns the randomly published host port.
# Example: http://localhost:<novnc_port>/vnc.html
```

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Browser
    participant Nginx as Frontend nginx 3000
    participant API as Backend FastAPI 8080
    participant SM as SessionManager
    participant DB as PostgreSQL
    participant VNC as Desktop container (Xvfb x11vnc noVNC)
    participant Runner as Runner (inside desktop container)
    participant Claude as Anthropic API

    Note over User,Claude: 1 Load UI
    User->>Browser: Open app
    Browser->>Nginx: GET /
    Nginx-->>Browser: index.html and app.js
    Browser->>API: GET /sessions

    Note over User,Claude: 2 New session
    User->>Browser: New Session
    Browser->>API: POST /sessions
    API->>SM: start_session_infra
    SM->>VNC: docker run new desktop container
    VNC-->>SM: novnc_port (published host port)
    API->>DB: INSERT AgentSession
    DB-->>API: ok
    API-->>Browser: session_id and ports

    Note over User,Claude: 3 Live desktop
    Browser->>VNC: iframe GET vnc.html on host novnc_port
    Note right of VNC: Host port is dynamically published per container

    Note over User,Claude: 4 History and SSE
    Browser->>API: GET /sessions/id/messages
    API->>DB: SELECT messages
    DB-->>API: rows
    API-->>Browser: JSON history
    Browser->>API: EventSource GET /sessions/id/stream
    API-->>Browser: SSE connected and pings

    Note over User,Claude: 5 Send task
    User->>Browser: Submit message
    Browser->>API: POST /sessions/id/message
    API->>DB: INSERT user message
    API->>API: create_task run agent
    API-->>Browser: 200 ok

    API->>Runner: spawn python module runner
    Runner->>DB: load conversation
    Runner->>Claude: messages plus tools
    Claude-->>Runner: tool_use or text
    loop Tool calls
        Runner->>VNC: xdotool GUI bash on DISPLAY (inside same container)
        Runner-->>API: JSON lines streamed back via docker exec
        API->>API: enqueue line
        API-->>Browser: SSE data
    end
    Runner-->>API: process exit
    API->>API: enqueue finished status
    API->>API: enqueue sentinel
    API-->>Browser: SSE status or ping
```