// Typed client for the FastAPI backend (services/backend). Phase 2 added a
// 3-way persona switcher (JARVIS/FRIDAY/ULTRON) -- persona is sent per
// message and can change mid-conversation. Phase 3 added long-term memory:
// chat replies can recall stored facts (surfaced as `recalled_memories` on
// each message), and /memory exposes a dashboard to view/edit/delete them.

import type { Persona, PersonaInfo } from "@/lib/personas";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "user" | "assistant";

export interface RecalledMemory {
  id: string;
  content: string;
  similarity: number;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  persona: Persona | null;
  created_at: string;
  // Snapshot of the memories injected into the prompt when this message was
  // generated -- required (not optional) because the backend always sends
  // it, so a missing field here is a real bug at the construction site, not
  // something to silently default away. See ChatMessageBubble/
  // RecalledMemoryChips for where this renders.
  recalled_memories: RecalledMemory[];
}

export interface ChatMessageResponse {
  conversation_id: string;
  message: ChatMessage;
  model_used: string;
  fell_back: boolean;
  // True when the persona's output filter (ULTRON only, today) replaced the
  // model's reply with a safety refusal.
  filtered: boolean;
}

export interface ConversationSummary {
  id: string;
  persona: Persona;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(0, "Could not reach the backend. Is it running on " + API_BASE_URL + "?");
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON; fall back to statusText
    }
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

export function sendMessage(
  content: string,
  conversationId?: string,
  persona?: Persona
): Promise<ChatMessageResponse> {
  return request<ChatMessageResponse>("/chat/message", {
    method: "POST",
    body: JSON.stringify({ content, conversation_id: conversationId ?? null, persona: persona ?? null }),
  });
}

export function listConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>("/chat/conversations");
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/chat/conversations/${id}`);
}

export function listPersonas(): Promise<PersonaInfo[]> {
  return request<PersonaInfo[]>("/personas");
}

// --- Phase 3: memory -------------------------------------------------

export type MemoryType = "short_term" | "long_term" | "episodic" | "semantic";

export interface Memory {
  id: string;
  content: string;
  memory_type: MemoryType;
  source: string;
  persona: Persona | null;
  created_at: string;
  updated_at: string;
  last_recalled_at: string | null;
  expires_at: string | null;
  embedding_pending: boolean;
}

export interface MemoryCreateResult {
  memory: Memory;
  deduplicated: boolean;
}

export function listMemories(q?: string): Promise<Memory[]> {
  const query = q ? `?q=${encodeURIComponent(q)}` : "";
  return request<Memory[]>(`/memory${query}`);
}

export function createMemory(content: string): Promise<MemoryCreateResult> {
  return request<MemoryCreateResult>("/memory", {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function updateMemory(id: string, content: string): Promise<Memory> {
  return request<Memory>(`/memory/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ content }),
  });
}

// Returns { deleted: 1 }, not void: the backend replies 200 with a body
// rather than 204, since request<T>() below always calls response.json().
export function deleteMemory(id: string): Promise<{ deleted: number }> {
  return request<{ deleted: number }>(`/memory/${id}`, { method: "DELETE" });
}

export function deleteAllMemories(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>("/memory/all?confirm=true", { method: "DELETE" });
}
