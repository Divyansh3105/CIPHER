# CIPHER — Multi-Persona AI Assistant

A full-stack AI assistant with three switchable personas — JARVIS (professional/strategic), FRIDAY (friendly/supportive), and ULTRON (analytical/dry-witted, safety-bounded) — featuring text + voice interaction, long-term memory, document-grounded RAG, and (in later phases) permissioned computer control.

## Status

🚧 Phase 0 complete — core scaffolding in place. Building Phase 1 (Core MVP) next.

## Architecture

See `docs/architecture.md` for the full system design, personality architecture, memory system, and roadmap.

## Tech Stack

- **Frontend:** Next.js, TypeScript, Tailwind CSS
- **Backend:** Python, FastAPI
- **Database:** PostgreSQL + pgvector (via Supabase)
- **Auth:** Supabase Auth
- **LLMs:** Gemini (primary), Groq (fast/fallback)

## Local Setup

### Backend

\`\`\`bash
cd services/backend
python -m venv venv
source venv/Scripts/activate # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env # fill in your keys
uvicorn app.main:app --reload
\`\`\`
Runs at http://localhost:8000 (docs at /docs)

### Frontend

\`\`\`bash
cd apps/web
npm install
npm run dev
\`\`\`
Runs at http://localhost:3000

## Roadmap

- [x] Phase 0 — Research & Planning
- [ ] Phase 1 — Core MVP (text chat)
- [ ] Phase 2 — Personality System
- [ ] Phase 3 — Memory
- [ ] Phase 4 — Voice
- [ ] Phase 5 — Tools & RAG
- [ ] Phase 6 — Multi-Agent System
- [ ] Phase 7 — Advanced Features (vision, computer control)
- [ ] Phase 8 — Production & Deployment
