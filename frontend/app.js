// Use the same host the user loaded the frontend from.
// This fixes "localhost" issues when accessing the UI remotely (e.g. VM/hosted box),
// where `localhost` would otherwise point at the *viewer’s* machine.
const HOST = window.location.hostname;
const PROTO = window.location.protocol; // "http:" or "https:"
const API_URL = `${PROTO}//${HOST}:8080`;

let activeSessionId = null;
let eventSource = null;

const elements = {
    sessionList: document.getElementById('sessionList'),
    newSessionBtn: document.getElementById('newSessionBtn'),
    vncContainer: document.getElementById('vncContainer'),
    chatHistory: document.getElementById('chatHistory'),
    chatForm: document.getElementById('chatForm'),
    taskInput: document.getElementById('taskInput'),
    sendBtn: document.getElementById('sendBtn'),
    activeSessionBar: document.getElementById('activeSessionBar')
};

async function fetchSessions() {
    try {
        const res = await fetch(`${API_URL}/sessions`);
        const sessions = await res.json();
        renderSessions(sessions);
    } catch (e) {
        console.error("Error fetching sessions", e);
    }
}

function renderSessions(sessions) {
    elements.sessionList.innerHTML = '';
    sessions.forEach(s => {
        const btn = document.createElement('button');
        btn.className = `w-full text-left px-3 py-2 text-sm rounded flex items-center gap-2 transition-all ${
            s.id === activeSessionId 
            ? 'bg-indigo-600 text-white shadow-md' 
            : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
        }`;
        btn.innerHTML = `
            <div class="w-2 h-2 rounded-full ${s.status === 'running' ? 'bg-emerald-400' : 'bg-slate-500'}"></div>
            <span class="truncate">Session ${s.id.substring(0, 6)}</span>
        `;
        btn.onclick = () => loadSession(s.id, s.novnc_port);
        elements.sessionList.appendChild(btn);
    });
}

function addChatMessage(role, text) {
    const div = document.createElement('div');
    div.className = `message-bubble p-3 rounded-lg text-sm ${
        role === 'user' 
        ? 'bg-indigo-600/20 border border-indigo-500/30 ml-8 text-indigo-100' 
        : 'bg-slate-800 border border-slate-700 mr-8 text-slate-300'
    }`;
    
    // Simple markdown formatting replacement for bold/code for demo
    const formatted = text.replace(/`([^`]+)`/g, '<code class="bg-black/30 px-1 py-0.5 rounded text-pink-300">$1</code>')
                          .replace(/\n/g, '<br>');
                          
    div.innerHTML = `
        <div class="font-bold text-xs mb-1 opacity-75">${role === 'user' ? 'You' : 'Agent'}</div>
        <div class="font-mono leading-relaxed">${formatted}</div>
    `;
    elements.chatHistory.appendChild(div);
    elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
}

function connectStream(sessionId) {
    if (eventSource) {
        eventSource.close();
    }
    
    eventSource = new EventSource(`${API_URL}/sessions/${sessionId}/stream`);
    
    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'output' && data.data.type === 'text') {
                 addChatMessage('assistant', data.data.text);
            } else if (data.type === 'status') {
                 addChatMessage('system', `> ${data.data.message}`);
            } else if (data.type === 'tool_result') {
                 addChatMessage('system', `Tool Result: ${data.data.output || data.data.error || 'Executed'}`);
            }
        } catch (e) {
            console.error("Failed to parse event", e);
        }
    };
    
    eventSource.onerror = (err) => {
        console.log("SSE error/closed");
    };
}

async function loadSession(sessionId, novncPort) {
    activeSessionId = sessionId;
    elements.taskInput.disabled = false;
    elements.sendBtn.disabled = false;
    elements.activeSessionBar.classList.remove('hidden');

    // Load VNC iframe — novnc_proxy serves the full HTML + WS on the same port
    elements.vncContainer.innerHTML = '';
    const iframe = document.createElement('iframe');
    iframe.src = `${PROTO}//${HOST}:${novncPort}/vnc.html?autoconnect=true&reconnect=true&resize=scale`;
    iframe.className = 'w-full h-full border-none bg-black';
    elements.vncContainer.appendChild(iframe);

    // Load history
    elements.chatHistory.innerHTML = '';
    try {
        const res = await fetch(`${API_URL}/sessions/${sessionId}/messages`);
        const messages = await res.json();
        messages.forEach(m => {
            const content = m.content;
            const txt = Array.isArray(content) && content[0]?.text
                ? content[0].text
                : (typeof content === 'string' ? content : JSON.stringify(content));
            addChatMessage(m.role, txt);
        });
    } catch(e) {
        console.error("Failed to load history", e);
    }

    connectStream(sessionId);
    fetchSessions(); // re-render sidebar
}

elements.newSessionBtn.onclick = async () => {
    try {
        const res = await fetch(`${API_URL}/sessions`, { method: 'POST' });
        const data = await res.json();
        await loadSession(data.session_id, data.novnc_port);
    } catch (e) {
        alert("Failed to start session. Ensure the backend is running.");
    }
};

elements.chatForm.onsubmit = async (e) => {
    e.preventDefault();
    if (!activeSessionId) return;
    
    const text = elements.taskInput.value.trim();
    if (!text) return;
    
    elements.taskInput.value = '';
    addChatMessage('user', text);
    
    try {
        await fetch(`${API_URL}/sessions/${activeSessionId}/message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        });
    } catch(e) {
         console.error("Send message failed", e);
    }
};

// Initial load
fetchSessions();
