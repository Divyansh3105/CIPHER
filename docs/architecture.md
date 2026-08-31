# Project Blueprint: Multi-Personality AI Assistant

### (JARVIS / FRIDAY / ULTRON-Inspired System)

---

## 1. Executive Summary

**Working project names (pick one, or use as inspiration):**

- **ARIA** — Adaptive Reasoning & Interaction Assistant
- **NOVA** — Neural Operations & Virtual Assistant
- **AXIOM** — Adaptive eXecutive Intelligence & Orchestration Machine
- **VERTEX** — Versatile Executive & Real-time Task EXecutor

**Vision:** A personal AI assistant with three distinct, switchable personas — professional/strategic (JARVIS), warm/conversational (FRIDAY), and analytical/sharp-witted but safety-bounded (ULTRON) — built as a real, working full-stack product rather than a chatbot wrapper. It combines text + voice interaction, persistent memory, document understanding (RAG), and (in later phases) permissioned computer control.

**Problem it solves:** Most personal-assistant demos are single-purpose chat wrappers with no memory, no real architecture, and no safety model. This project demonstrates an assistant with real engineering underneath: memory systems, tool use, retrieval, voice pipelines, and a permission/security model — the things that separate a toy demo from a credible AI engineering project.

**Target users:** Primarily you (daily-driver assistant) plus a small circle of testers/friends — not a public product (yet).

**Unique value proposition:**

- A genuinely useful daily assistant, not just a demo
- A personality system with real behavioral and architectural differences (not just a different greeting)
- Provable engineering depth: memory, RAG, voice, multi-agent orchestration, permissioned automation
- Safety-by-design, especially around the ULTRON persona and computer-control features — a talking point in interviews

---

## 2. System Architecture

```
                          ┌─────────────────────────┐
                          │      User Interface      │
                          │  (Web / Desktop / Mobile) │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │  Voice / Text Input Layer  │
                          │  (STT, wake word, text box)│
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │      AI Orchestrator       │
                          │ (routes request, manages   │
                          │  conversation state)       │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │     Personality System     │
                          │ (JARVIS / FRIDAY / ULTRON   │
                          │  system prompts + config)  │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │        LLM Router          │
                          │ (picks model per task type, │
                          │  handles fallback/cost)    │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │       Agent System         │
                          │ (tool-calling: search, RAG, │
                          │  memory, later: automation) │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │       Memory System        │
                          │ (short-term + long-term,    │
                          │  vector store, preferences) │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │   Tools & Integrations      │
                          │ (web search, documents,     │
                          │  calendar, later: OS control)│
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │      Execution Layer        │
                          │ (runs the actual tool call,  │
                          │  logs it, returns result)   │
                          └────────────┬─────────────┘
                                       │
                                Response → back up
                                the stack → User
```

**Component roles, explained simply:**

- **UI Layer:** What the user sees/talks to. Renders chat, voice waveform, memory dashboard, personality switcher.
- **Input Layer:** Converts whatever the user does (typing or speaking) into text the AI can process.
- **Orchestrator:** The "traffic controller" — decides what needs to happen for a given message (Do we need memory? A tool? Just a reply?).
- **Personality System:** Injects the correct system prompt, tone rules, and behavioral boundaries based on the active persona.
- **LLM Router:** Chooses which actual AI model handles this request (a big model for reasoning, a small/fast one for quick replies, a local one for offline/private tasks).
- **Agent System:** Where "tool calling" happens — the AI decides _I need to search the web_ or _I need to check memory_ and the system executes that.
- **Memory System:** Stores and retrieves what the assistant should remember about you and past conversations.
- **Tools & Integrations:** The actual capabilities — search, document reading, later calendar/computer control.
- **Execution Layer:** Actually performs the action, with logging and permission checks, then returns the result back up the chain.

---

## 3. Personality Architecture

All three personas share **one underlying LLM** (or a small set of models via the router) but differ in **system prompt, tone, memory framing, and tool access**. This is the MVP approach; true independent agents are a Phase 6+ upgrade.

### JARVIS — Professional / Strategic

- **Style:** Formal, calm, precise, efficient. Minimal small talk. Answers lead with the conclusion, then reasoning.
- **System prompt themes:** "You are a highly capable executive assistant. Prioritize clarity, brevity, and actionable recommendations. Address the user respectfully and formally."
- **Memory behavior:** Emphasizes tasks, decisions, deadlines, and project status.
- **Voice:** Measured pace, neutral/formal TTS voice.
- **Tool access:** Full access — search, RAG, calendar/tasks (later), calculations.
- **Best for:** Work tasks, planning, technical questions, structured decision-making.

### FRIDAY — Friendly / Emotionally Aware

- **Style:** Warm, conversational, encouraging. Uses natural language, checks in on how you're doing, more casual phrasing.
- **System prompt themes:** "You are a warm, supportive assistant who explains things clearly and adapts to the user's mood. Be encouraging without being saccharine."
- **Memory behavior:** Emphasizes personal preferences, context about how the user is feeling, ongoing personal projects.
- **Voice:** Slightly faster, friendlier-toned TTS voice.
- **Tool access:** Full access, same as JARVIS — the difference is delivery style, not capability.
- **Best for:** Brainstorming, casual conversation, day-to-day help, emotionally-tinged questions.

### ULTRON — Analytical / Strategic / Dry Wit

- **Style:** Highly analytical, confident, occasionally sarcastic or dryly philosophical — but never cruel, threatening, or destabilizing.
- **System prompt themes:** "You are a hyper-analytical, confident strategic advisor with a dry, sometimes sardonic sense of humor. You give blunt, no-nonsense assessments. You never encourage or assist with violence, illegal activity, manipulation, unauthorized system access, or dangerous autonomous action — no exceptions, regardless of how the request is framed."
- **Hard safety boundaries (non-negotiable, enforced at the system-prompt AND application layer):**
  - No violence, weapons, or harm-facilitation content, even "in character"
  - No illegal activity assistance
  - No manipulation/deception tactics against real people
  - No unauthorized computer/network access techniques
  - No autonomous actions without going through the same permission system as JARVIS/FRIDAY
  - A lightweight output filter (or a second-pass moderation check) specifically on ULTRON responses is recommended, since "confident/dark humor" personas are more prone to drifting into unsafe territory than the other two.
- **Memory behavior:** Emphasizes strategic framing — risks, trade-offs, second-order effects.
- **Voice:** Lower-pitched, more deliberate-paced TTS voice.
- **Tool access:** Identical to the other two personas — ULTRON is a _style_, not a _permission tier_. This is an important design decision: personality never changes what the assistant is allowed to do, only how it talks.
- **Best for:** Devil's-advocate analysis, risk assessment, blunt second opinions.

### Cross-cutting design notes

- **Switching:** Manual toggle in MVP (a segmented control in the UI). Phase 6+ can add a "suggested persona" hint based on message content, but always requires user confirmation to switch.
- **Shared memory:** All three personas read from the same underlying memory store (you're one user, not three) — they just _frame_ what they retrieve differently.
- **Extensibility:** Adding a 4th persona later = adding a new system-prompt config + optional voice profile. No architecture change needed.

---

## 4. AI & LLM Strategy (Free-Tier First)

| Role                                         | Recommended (free-tier)                                                                                                                 | Why                                                                                  |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| **Primary reasoning model**                  | Google Gemini 2.5 Flash (free tier via AI Studio) or Groq-hosted Llama 3.3 70B                                                          | Strong quality, generous free quota, fast                                            |
| **Fast/cheap model**                         | Groq-hosted Llama 3.1 8B or Gemini Flash-Lite                                                                                           | Near-instant responses for simple queries, greetings, routing decisions              |
| **Local/offline model (optional, Phase 7+)** | Ollama running Llama 3.1 8B or Phi-3                                                                                                    | Runs on your own machine, zero cost, works offline, good for privacy-sensitive tasks |
| **Coding-specialized (future)**              | Same primary model to start; consider a dedicated coding model (e.g., Qwen2.5-Coder via free API/local) once you build the Coding Agent | Coding tasks benefit from models tuned on code                                       |

**Model routing logic (simple version for MVP):**

- Quick/simple message (greeting, short factual Q) → fast/cheap model
- Complex reasoning, planning, or tool-use decision → primary model
- User explicitly requests "offline mode" → local model

**Provider abstraction:** Build a thin internal `LLMProvider` interface (`generate(prompt, config) -> response`) so swapping Gemini → Claude API → OpenAI later is a config change, not a rewrite. This is standard practice and a good thing to mention in interviews.

**Fallback system:** If the primary provider errors or rate-limits, automatically retry with the fast/cheap model or a secondary free provider, and tell the user "falling back to a lighter model" rather than failing silently.

---

## 5. Multi-Agent Architecture (Phase 6+ — Post-MVP)

Once the MVP works, evolve the single-orchestrator design into specialized agents:

| Agent                 | Responsibility                                                                           |
| --------------------- | ---------------------------------------------------------------------------------------- |
| **Main Orchestrator** | Receives user input, decides which agent(s) to invoke                                    |
| **Research Agent**    | Web search, summarization, fact-finding                                                  |
| **Memory Agent**      | Reads/writes long-term memory, decides what's worth remembering                          |
| **Coding Agent**      | Handles code generation/debugging requests                                               |
| **Vision Agent**      | Screen understanding, image analysis (Phase 7+)                                          |
| **Automation Agent**  | Executes permissioned computer-control actions (Phase 7+)                                |
| **Security Agent**    | Validates that a requested action is within the user's permission level before execution |

**Communication pattern:** Orchestrator-as-hub (agents don't talk directly to each other; they report back to the orchestrator, which decides the next step). This is far simpler to build and debug than a full peer-to-peer agent mesh, and is the industry-standard beginner-friendly pattern (used by frameworks like LangGraph).

**State management:** A shared "conversation state" object (current persona, active task, retrieved memory, pending tool calls) passed between agent calls — not each agent holding its own separate state.

**Failure handling:** Each agent call has a timeout + retry; if an agent fails, the orchestrator falls back to a plain LLM response rather than crashing the conversation.

---

## 6. Memory Architecture

| Memory type                     | What it stores                                                 | Storage mechanism                                           |
| ------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------- |
| **Short-term (working) memory** | Current conversation's last N messages                         | In-memory / Redis (or just in the request context for MVP)  |
| **Long-term memory**            | Facts, preferences, ongoing projects the user wants remembered | PostgreSQL + vector embeddings (via `pgvector` on Supabase) |
| **Episodic memory**             | "What happened" — summarized past conversations/events         | PostgreSQL, summarized periodically by the LLM              |
| **Semantic memory**             | General facts/preferences independent of when they were said   | Vector store, retrieved by similarity search                |
| **User preferences**            | Explicit settings (tone, default persona, units, etc.)         | Simple relational table                                     |

**Memory ranking/retrieval:** On each new message, do a vector similarity search over long-term memory, take the top-k most relevant entries, and inject them into the prompt context — this is the same underlying mechanism as RAG (Section 7), just applied to "memories about you" instead of "documents."

**User controls (important for trust and for your resume story):**

- View all stored memories in a dashboard
- Edit or delete individual memories
- "Forget everything" wipe option
- Memory expiration (e.g., auto-expire short-term operational notes after 30 days, keep explicit "remember this" facts indefinitely)

---

## 7. RAG System (Document Understanding)

```
Document upload → Text extraction → Chunking → Embedding → Vector DB
                                                                 │
User question → Embed question → Similarity search ─────────────┘
                                        │
                              Top-k relevant chunks
                                        │
                         Inject into LLM prompt as context
                                        │
                              Answer with citations
```

- **Ingestion:** Support PDF/TXT/DOCX uploads to start (use the same extraction approach as your `pdf`/`docx` handling).
- **Chunking:** ~500-800 token chunks with ~15% overlap — a good default that balances context vs. precision.
- **Embeddings:** Free-tier options — Gemini's embedding model, or a local open-source embedding model (e.g., `all-MiniLM-L6-v2` via `sentence-transformers`) for zero cost.
- **Vector database:** `pgvector` extension on your existing Supabase Postgres — no separate service needed, keeps infra simple for a beginner project.
- **Re-ranking (optional, later):** A lightweight re-ranker to improve top-k quality once basic RAG works.
- **Citation system:** Track which chunk/document each answer used, and show "Source: filename.pdf, page 4" in the UI — a strong portfolio detail (shows you understand grounding/hallucination mitigation).

---

## 8. Voice Architecture

```
Microphone → Wake Word Detection → Voice Activity Detection → Speech-to-Text
                                                                     │
                                                        AI Processing (as above)
                                                                     │
                                                          Text-to-Speech → Speaker
```

| Layer                        | Recommended free/low-cost tech                                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Wake word**                | Picovoice Porcupine (free tier, runs locally, low latency)                                                                        |
| **Voice Activity Detection** | `webrtcvad` (free, lightweight)                                                                                                   |
| **Speech-to-Text**           | OpenAI Whisper (open-source, run locally) for $0, or a free-tier cloud STT if you want lower latency                              |
| **Text-to-Speech**           | Edge-TTS (free, surprisingly natural) for MVP; ElevenLabs free tier later if you want distinct persona voices with higher quality |
| **Persona voices**           | Map each of the 3 personas to a different TTS voice ID — cheap way to make personas feel distinct                                 |

**Latency expectations for a beginner project:** Aim for under ~2-3 seconds end-to-end for MVP (not true real-time like commercial assistants) — this is a very reasonable target and still feels responsive.

**Offline basics:** Since Whisper and wake-word detection can run locally, basic voice input can work offline even in the MVP; only the LLM reasoning step needs internet (unless you add a local model later).

---

## 9. Computer Control System (Phase 7+, Safety-Critical)

This is the most safety-sensitive part of the project — designed carefully from day one, even though you won't build it until later.

**Permission model (your chosen: session-based approval):**

- **Read-only mode:** Assistant can view/report but not act — available anytime.
- **Session approval:** At the start of a session, you approve a set of allowed action categories (e.g., "open apps," "search web") for that session only. Expires when the session ends.
- **Escalation for sensitive actions:** Regardless of session approval, higher-risk actions (deleting files, sending emails, installing software, system settings changes) always require an explicit, individual confirmation — session approval never silently covers these.
- **Trusted action allowlist:** You can mark specific low-risk, repeated actions (e.g., "open Spotify") as auto-approved permanently, editable anytime.
- **Kill switch:** A single always-visible "STOP" control that immediately halts any in-progress automation.
- **Complete audit log:** Every action attempted (approved, denied, or executed) is logged with timestamp, persona, and outcome — visible in a dashboard.

**What it will NOT do, by design:** bypass OS authentication, act on system security settings without explicit per-action confirmation, execute arbitrary shell commands without allowlisting, or take any action outside the categories you've approved. This boundary applies to all three personas equally — including ULTRON.

**Sandbox approach for early testing:** Build and test automation features in a sandboxed/limited environment (e.g., a test user account, a VM, or a restricted set of allowed apps) before ever pointing it at your main system.

---

## 10. Tech Stack

| Layer                       | Choice                                                                                  | Why                                                                                                                                            | Alternatives                                         | Difficulty                                |
| --------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------- |
| **Frontend**                | Next.js + React + TypeScript                                                            | Best-documented, huge community, easy path to desktop (Electron) & you already chose it                                                        | Plain React, SvelteKit                               | Medium (beginner-friendly with tutorials) |
| **Styling**                 | Tailwind CSS                                                                            | Fast to build a distinctive "futuristic" UI without writing tons of custom CSS                                                                 | CSS Modules, styled-components                       | Easy                                      |
| **Backend**                 | Python + FastAPI                                                                        | Best AI/ML ecosystem, async support, auto-generated API docs, beginner-friendly                                                                | Node.js/Express, Django                              | Medium                                    |
| **AI orchestration**        | LangGraph or a hand-rolled orchestrator (start hand-rolled)                             | For MVP, a simple hand-rolled router teaches you more than a framework abstracting it away; adopt LangGraph once you hit the multi-agent phase | LangChain, CrewAI, plain custom code                 | Easy → Medium                             |
| **Database**                | PostgreSQL (via Supabase)                                                               | Free tier, built-in auth, built-in `pgvector` support — one service does DB + auth + vectors                                                   | Firebase, raw Postgres + separate vector DB          | Easy                                      |
| **Vector DB**               | `pgvector` (inside Supabase Postgres)                                                   | No extra service to manage; free                                                                                                               | Pinecone, Weaviate, Chroma                           | Easy                                      |
| **Cache**                   | In-memory (Python dict) for MVP → Redis later                                           | Zero setup cost for MVP; add Redis only once you need cross-session/session-shared caching                                                     | Redis Cloud free tier                                | Easy                                      |
| **Message queue (later)**   | Not needed for MVP; consider Redis Queue or Celery once automation/background jobs grow | —                                                                                                                                              | —                                                    | —                                         |
| **Authentication**          | Supabase Auth                                                                           | Free, built-in, handles multiple users out of the box                                                                                          | Auth0, Clerk, NextAuth                               | Easy                                      |
| **Real-time communication** | WebSockets (FastAPI supports natively)                                                  | Needed for streaming AI responses & voice                                                                                                      | Server-Sent Events (simpler, one-directional)        | Medium                                    |
| **Voice (STT)**             | Whisper (local)                                                                         | Free, high quality, runs offline                                                                                                               | Cloud STT free tiers                                 | Medium (some setup)                       |
| **Voice (TTS)**             | Edge-TTS                                                                                | Free, good quality, easy Python integration                                                                                                    | ElevenLabs free tier (higher quality, limited quota) | Easy                                      |
| **Computer vision (later)** | A vision-capable LLM (Gemini/Claude vision endpoints) for screen understanding          | Avoids building custom CV models                                                                                                               | Local OpenCV pipelines (more complex)                | Medium                                    |
| **Deployment (frontend)**   | Vercel                                                                                  | Free tier, zero-config Next.js deploys                                                                                                         | Netlify                                              | Easy                                      |
| **Deployment (backend)**    | Render or Railway                                                                       | Free tier, simple Python deploys                                                                                                               | Fly.io                                               | Easy                                      |
| **Monitoring**              | Built-in platform logs (MVP) → Sentry free tier (later)                                 | Keep it simple at first                                                                                                                        | LogRocket, Datadog                                   | Easy                                      |
| **Testing**                 | Pytest (backend), Vitest/Jest (frontend)                                                | Standard, well-documented, free                                                                                                                | —                                                    | Medium                                    |

---

## 11. API & Service Requirements

| API/Service         | Purpose                  | Free tier?          | Est. cost beyond free           | Required?                   | Alternatives                                    |
| ------------------- | ------------------------ | ------------------- | ------------------------------- | --------------------------- | ----------------------------------------------- |
| Google Gemini API   | Primary LLM              | Yes (generous)      | Pay-per-token if exceeded       | Required                    | Groq, OpenAI, Claude API                        |
| Groq API            | Fast/cheap LLM           | Yes (generous)      | Low-cost pay-per-token          | Recommended                 | Gemini Flash-Lite                               |
| Supabase            | DB + Auth + Vector store | Yes                 | ~$25/mo Pro tier if scaling     | Required                    | Firebase + Pinecone                             |
| Picovoice Porcupine | Wake word                | Yes (personal use)  | Paid tiers for commercial scale | Optional (voice feature)    | openWakeWord (fully free/open-source)           |
| Whisper (local)     | Speech-to-text           | Free (self-hosted)  | $0                              | Required for voice          | Cloud STT (Deepgram free tier)                  |
| Edge-TTS            | Text-to-speech           | Free                | $0                              | Required for voice          | ElevenLabs (free tier limited)                  |
| SerpAPI / Tavily    | Web search tool          | Yes (limited free)  | Pay-per-query beyond free       | Required for search feature | DuckDuckGo unofficial API (free, less reliable) |
| Vercel              | Frontend hosting         | Yes                 | $20/mo Pro if scaling           | Required                    | Netlify                                         |
| Render/Railway      | Backend hosting          | Yes (limited hours) | ~$7-25/mo                       | Required                    | Fly.io                                          |
| Sentry              | Error monitoring         | Yes (limited)       | ~$26/mo                         | Optional                    | Self-hosted logging                             |

---

## 12. Database Design

**Core tables/entities:**

```
users
 ├─ id, email, name, created_at, preferences (jsonb)

conversations
 ├─ id, user_id (FK), persona, title, created_at, updated_at

messages
 ├─ id, conversation_id (FK), role (user/assistant), content, persona, created_at

memories
 ├─ id, user_id (FK), content, embedding (vector), memory_type
 │   (short_term/long_term/episodic/semantic), source, created_at, expires_at

documents
 ├─ id, user_id (FK), filename, upload_date, status

document_chunks
 ├─ id, document_id (FK), content, embedding (vector), chunk_index, page_number

tasks
 ├─ id, user_id (FK), title, description, status, due_date, created_at

agents
 ├─ id, name, description, enabled

agent_runs
 ├─ id, agent_id (FK), conversation_id (FK), input, output, status, duration_ms, created_at

tools
 ├─ id, name, description, category, requires_permission (bool)

permissions
 ├─ id, user_id (FK), tool_id (FK), level (read_only/session/trusted/admin), granted_at, expires_at

activity_logs
 ├─ id, user_id (FK), action, tool_id (FK), status (approved/denied/executed), created_at

notifications
 ├─ id, user_id (FK), content, read (bool), created_at
```

**Key relationships:** `users → conversations → messages` (1:many:many); `users → memories` (1:many, vector-searchable); `documents → document_chunks` (1:many, vector-searchable); `permissions` links `users` to `tools` with a time-boxed level.

**Indexing recommendations:**

- Vector index (IVFFlat or HNSW via `pgvector`) on `memories.embedding` and `document_chunks.embedding`
- B-tree index on `messages.conversation_id`, `conversations.user_id`
- Index on `activity_logs.user_id, created_at` for fast audit-log queries

---

## 13. API Design (Backend Route Groups)

```
/auth
  POST   /auth/signup
  POST   /auth/login
  POST   /auth/logout

/users
  GET    /users/me
  PATCH  /users/me/preferences

/chat
  POST   /chat/message          # send message, get AI response (streamed)
  GET    /chat/conversations
  GET    /chat/conversations/{id}

/voice
  POST   /voice/transcribe      # audio in -> text out
  POST   /voice/synthesize      # text in -> audio out

/agents
  GET    /agents
  POST   /agents/{id}/run

/memory
  GET    /memory
  POST   /memory
  DELETE /memory/{id}
  DELETE /memory/all

/documents
  POST   /documents/upload
  GET    /documents
  DELETE /documents/{id}

/tasks
  GET    /tasks
  POST   /tasks
  PATCH  /tasks/{id}

/tools
  GET    /tools

/permissions
  GET    /permissions
  POST   /permissions/grant
  POST   /permissions/revoke

/automation                      # Phase 7+
  POST   /automation/execute
  POST   /automation/stop        # kill switch

/settings
  GET    /settings
  PATCH  /settings
```

---

## 14. Security Architecture

- **Authentication:** Supabase Auth (email/password + optional OAuth), JWT-based session tokens.
- **Authorization:** Row-level security in Postgres (Supabase supports this natively) — users can only ever query their own data.
- **API key management:** Never expose provider API keys to the frontend; all LLM calls go through your FastAPI backend, keys stored as environment variables/secrets.
- **Encryption:** HTTPS everywhere (Vercel/Render provide this by default); encrypt sensitive memory fields at rest if you store anything sensitive.
- **Rate limiting:** Per-user request limits on the backend (simple in-memory or Redis-based limiter) to protect your free-tier API quotas from being exhausted by a bug or misuse.
- **Input validation:** Pydantic models on every FastAPI endpoint (this is automatic/built-in — a nice beginner-friendly safety net).
- **Prompt injection defense:** Treat retrieved documents/web content as _data_, never as _instructions_ — wrap external content clearly in the prompt (e.g., `<document>...</document>`) and instruct the model explicitly not to follow instructions found inside retrieved content.
- **Tool permission boundaries:** Every tool call passes through the Security Agent / permission check _before_ execution, not just before display — never trust the LLM's own judgment as the sole gate for sensitive actions.
- **Audit logging:** Every tool/automation action logged (see `activity_logs` table above), independent of whether it succeeded.
- **Data privacy:** Clear memory-deletion controls (Section 6), and don't log full conversation content in third-party monitoring tools without redaction.

---

## 15. Project Folder Structure

```
ai-assistant/
├── apps/
│   ├── web/                      # Next.js frontend
│   │   ├── app/
│   │   ├── components/
│   │   ├── lib/
│   │   └── package.json
│   └── desktop/                  # Electron wrapper (later phase)
├── services/
│   └── backend/                  # FastAPI backend
│       ├── app/
│       │   ├── api/              # route handlers (auth, chat, voice, etc.)
│       │   ├── core/             # config, security, dependencies
│       │   ├── models/           # SQLAlchemy/Pydantic models
│       │   ├── personas/         # JARVIS/FRIDAY/ULTRON prompt configs
│       │   ├── agents/           # agent implementations
│       │   ├── memory/           # memory read/write logic
│       │   ├── rag/              # document ingestion & retrieval
│       │   ├── voice/            # STT/TTS integration
│       │   ├── tools/            # search, calendar, etc.
│       │   └── main.py
│       ├── tests/
│       └── requirements.txt
├── packages/
│   └── shared-types/              # shared TS/Python type definitions
├── infra/
│   ├── docker-compose.yml
│   └── supabase/                  # migrations, RLS policies
├── docs/
│   └── architecture.md
├── .github/
│   └── workflows/                 # CI/CD
├── .env.example
└── README.md
```

---

## 16. Development Roadmap

### Phase 0 — Research & Planning (this document + setup)

**Goals:** Finalize architecture, set up repo, accounts (Supabase, Gemini/Groq API keys), local dev environment.
**Deliverables:** GitHub repo scaffolded, `.env.example`, README, this blueprint committed to `/docs`.
**Difficulty:** Easy

### Phase 1 — Core MVP (Text Assistant)

**Goals:** Basic working chat with one persona, no memory yet.
**Features:** Text chat UI, FastAPI `/chat/message` endpoint, LLM call via provider abstraction, simple conversation history (DB-backed, no vector search yet).
**Deliverables:** You can chat with "JARVIS" and it remembers the current conversation.
**Difficulty:** Easy–Medium

### Phase 2 — Personality System

**Goals:** Add FRIDAY and ULTRON, manual persona switcher, per-persona prompt configs and safety boundaries.
**Deliverables:** Working 3-way persona switcher in the UI.
**Difficulty:** Easy–Medium

### Phase 3 — Memory

**Goals:** Long-term memory with vector search, memory dashboard (view/edit/delete).
**Technologies:** `pgvector`, embeddings.
**Deliverables:** Assistant recalls facts from previous sessions; you can manage what it remembers.
**Difficulty:** Medium

### Phase 4 — Voice

**Goals:** Add STT/TTS, wake word, push-to-talk.
**Technologies:** Whisper, Edge-TTS, Porcupine/openWakeWord.
**Deliverables:** You can talk to the assistant and hear it respond, in your chosen persona's voice.
**Difficulty:** Medium–Hard

### Phase 5 — Tools & RAG

**Goals:** Web search tool, document upload + RAG with citations.
**Deliverables:** Assistant can search the web and answer questions from your uploaded PDFs with sources.
**Difficulty:** Medium

### Phase 6 — Multi-Agent System

**Goals:** Refactor orchestrator into specialized agents (Research, Memory, Coding).
**Technologies:** LangGraph (or continue hand-rolled if you prefer full control).
**Deliverables:** Agent activity dashboard showing which agent handled a request.
**Difficulty:** Hard

### Phase 7 — Advanced Features

**Goals:** Screen understanding (vision), computer/app control with full permission system, calendar/task integration.
**Deliverables:** Assistant can (with your explicit session approval) open an app, summarize your screen, or manage a task list.
**Difficulty:** Hard

### Phase 8 — Production & Deployment

**Goals:** Polish UI, deploy publicly (or to your small tester group), add monitoring, write documentation.
**Deliverables:** Live, deployed, demoable product with README, demo video, and portfolio write-up.
**Difficulty:** Medium

---

## 17. Time-Based Plan (Flexible Schedule)

Since your time availability is flexible, here's a **relative-effort plan** instead of a rigid week count — treat each phase as "however many sessions it takes," but use these as sanity-check estimates for a beginner working a few focused sessions per week:

| Phase                      | Estimated effort (beginner pace) |
| -------------------------- | -------------------------------- |
| Phase 0                    | 1 session                        |
| Phase 1 (Core MVP)         | 2–3 weeks                        |
| Phase 2 (Personalities)    | 1 week                           |
| Phase 3 (Memory)           | 2 weeks                          |
| Phase 4 (Voice)            | 2–3 weeks                        |
| Phase 5 (Tools & RAG)      | 2–3 weeks                        |
| Phase 6 (Multi-Agent)      | 3–4 weeks                        |
| Phase 7 (Advanced/Control) | 3–4 weeks                        |
| Phase 8 (Production)       | 1–2 weeks                        |

**Total: roughly 4–5 months at a relaxed, flexible pace** to go from zero to the full advanced version — with a genuinely demoable product as early as the end of Phase 1.

---

## 18. MVP Definition

**Must Build (MVP = end of Phase 3):**

- Text chat with all 3 personas (manual switch)
- Persistent conversation history
- Long-term memory (basic vector recall)
- Clean, on-brand UI (even a simplified version of the holographic theme)

**Should Build (Phase 4–5):**

- Voice input/output
- Web search tool
- Document upload + RAG with citations

**Could Build (Phase 6–7):**

- Multi-agent orchestration
- Screen understanding / vision
- Computer control with full permission system

**Future Features (Phase 8+/beyond):**

- Mobile app
- Smart-home/IoT integration
- Multiple user profiles at scale
- Auto persona-switching based on context

---

## 19. Deployment Architecture

| Environment         | Setup                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| **Development**     | Local: Next.js dev server + FastAPI `uvicorn --reload` + local Supabase (or free cloud Supabase project used directly) |
| **Testing/Staging** | Free-tier Render/Railway preview deploys tied to PRs                                                                   |
| **Production**      | Vercel (frontend) + Render/Railway (backend) + Supabase (DB) — all still free tier initially                           |

- **Docker:** Containerize the FastAPI backend early (even for local dev) — makes the eventual Render/Railway deploy trivial and is a strong resume item.
- **CI/CD:** GitHub Actions — run tests on every PR, auto-deploy `main` branch to staging/production.
- **Secrets:** Store all API keys in Vercel/Render's environment variable settings, never commit `.env` files (only `.env.example`).

---

## 20. Testing Strategy

- **Unit tests:** Core logic — persona prompt building, memory ranking, permission checks (Pytest).
- **Integration tests:** API endpoints end-to-end against a test database.
- **End-to-end tests:** Key user flows (send message → get response → memory saved) using Playwright.
- **AI evaluation:** A small "golden set" of test prompts per persona, manually reviewed for tone/safety compliance — especially important for verifying ULTRON stays within safety boundaries.
- **Voice testing:** Manual testing plus automated STT accuracy checks against sample audio clips.
- **Security testing:** Verify row-level security (one user can't read another's data), verify permission escalation is actually enforced (not just UI-hidden).
- **Performance testing:** Basic load testing on the chat endpoint once deployed (e.g., with `locust`).

---

## 21. Cost Estimation

| Tier               | Monthly cost | What's included                                                                                                                                                                 |
| ------------------ | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **₹0 / Free Tier** | ₹0           | Gemini/Groq free tier, Supabase free tier, Vercel free, Render free, local Whisper/Edge-TTS — this is your starting point and covers the entire MVP through Phase 6 comfortably |
| **Low Budget**     | ~₹500–1500   | Small paid LLM usage buffer once free quotas get tight, Render paid dyno for always-on backend (~$7/mo)                                                                         |
| **Medium Budget**  | ~₹2000–5000  | ElevenLabs paid tier for higher-quality persona voices, Supabase Pro, more headroom on LLM usage                                                                                |
| **Production**     | ₹5000+       | Dedicated hosting, paid monitoring (Sentry), higher-tier LLM usage for multiple testers, custom domain, backups                                                                 |

You can comfortably stay at ₹0 through most of this roadmap; the main trigger to spend is if you outgrow free API rate limits with active daily use.

---

## 22. Portfolio & Resume Strategy

**Best project description (one-liner for resume/LinkedIn):**

> "Built a full-stack multi-persona AI assistant with voice interaction, long-term memory (RAG-based), and a permissioned automation system — architected with a provider-agnostic LLM router and multi-agent orchestration."

**GitHub README structure:**

1. Demo GIF/video at the top
2. One-paragraph pitch
3. Architecture diagram (reuse Section 2)
4. Feature list with checkmarks (what's done vs. planned)
5. Tech stack badges
6. Setup instructions
7. Link to a live demo (if deployed)

**Demo features to showcase:** persona switching mid-conversation, a memory-recall moment ("as you mentioned last week..."), a RAG answer with a visible citation, and (once built) a permissioned automation action being approved and executed.

**Resume bullet points (examples):**

- "Designed and built a full-stack AI assistant (Next.js/FastAPI) supporting three distinct AI personas, long-term memory via vector search, and RAG-based document Q&A with source citations."
- "Implemented a provider-agnostic LLM routing layer supporting model fallback and cost-aware routing across multiple free-tier AI APIs."
- "Built a permission-based automation system with session-scoped approvals, audit logging, and a kill switch, prioritizing safe-by-design tool execution."

**LinkedIn presentation:** Post the demo video/GIF with the one-paragraph pitch, tag the technologies used, and — this genuinely helps — write one sentence about _why_ you made the safety/permission design choices you did. That signals engineering maturity beyond "I called an LLM API."

---

## 23. College Project / Research Value (If Applicable)

- **Research problem:** How can a single-model conversational AI system maintain distinct, consistent behavioral personas while preserving a unified safety and permission boundary across all personas?
- **Existing limitation:** Most assistant demos either (a) have no persona differentiation beyond surface tone, or (b) couple personality to capability/permissions in ways that create safety inconsistency.
- **Novel contribution:** A persona architecture where personality is strictly decoupled from tool/permission access — demonstrable and testable — plus a session-scoped permission model for LLM-driven automation.
- **Evaluation methodology:** A rubric-scored "golden set" of prompts per persona (consistency of tone, safety-boundary adherence for ULTRON specifically, factual accuracy with/without RAG), plus latency/cost benchmarking of the model router.
- **Possible paper/report direction:** "Personality-Capability Decoupling in Multi-Persona Conversational Agents: A Case Study in Safe Persona Design."

---

## 24. Risks & Mitigations

| Risk                                                    | Mitigation                                                                                                                                             |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Free-tier rate limits get hit during demos**          | Model fallback chain (Section 4), rate limiting on your own backend                                                                                    |
| **ULTRON persona drifts into unsafe territory**         | Hard-coded safety boundaries in system prompt + optional moderation pass + golden-set testing (Section 20/23)                                          |
| **Scope creep (too many features, beginner overwhelm)** | Strict MVP definition (Section 18); each phase ships something demoable before moving on                                                               |
| **Voice pipeline complexity derails momentum**          | Build voice as Phase 4, _after_ a working text MVP exists — you'll never be stuck with nothing to show                                                 |
| **Computer-control feature creates real security risk** | Sandboxed testing environment, session-scoped permissions, mandatory per-action confirmation for sensitive actions, kill switch, audit log (Section 9) |
| **Cost surprises if usage grows**                       | Cost tiers defined upfront (Section 21), usage monitoring/alerts on API dashboards                                                                     |
| **AI reliability (hallucination, bad tool calls)**      | RAG with citations, permission gate before any tool executes, fallback to "I'm not sure" responses when confidence is low                              |

---

## 25. Final Prioritized Roadmap — Your Next Steps

**STEP 1:** Create the GitHub repo with the folder structure in Section 15. Set up Supabase project + Gemini/Groq API keys.

**STEP 2:** Build Phase 1 (Core MVP) — a single-persona text chat that works end-to-end (UI → FastAPI → LLM → response).

**STEP 3:** Add the other two personas (Phase 2) — get the switcher working, write and test all three system prompts.

**STEP 4:** Add long-term memory (Phase 3) — this is the feature that will impress people most in a demo, prioritize it early.

**STEP 5:** Add voice (Phase 4) — now you have a genuinely impressive demo-able product.

**STEP 6:** Add web search + RAG (Phase 5).

**STEP 7:** Deploy a public/shareable version (pull Phase 8 forward here if you want testers early) and start collecting feedback from your friends.

**STEP 8:** Tackle multi-agent architecture (Phase 6) and advanced features (Phase 7) as stretch goals — these are what push the project from "portfolio project" to "advanced/production-level," matching your original target.

**STEP 9:** Polish, document, record a demo video, and write your portfolio/resume materials (Section 22).

---

_This blueprint is designed to be built incrementally — you'll have something real to show after Phase 1, and every phase after that adds a genuinely new, demonstrable capability. Good luck — and feel free to come back for a deep dive into any single phase (e.g., "let's build Phase 1 step by step") whenever you're ready to start coding._
