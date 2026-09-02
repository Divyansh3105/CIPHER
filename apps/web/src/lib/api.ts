// Typed client for the FastAPI backend (services/backend). Phase 1 only:
// send a message, list conversations, fetch one conversation's history.

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  persona: string | null;
  created_at: string;
}

export interface ChatMessageResponse {
  conversation_id: string;
  message: ChatMessage;
  model_used: string;
  fell_back: boolean;
}

export interface ConversationSummary {
  id: string;
  persona: string;
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

export function sendMessage(content: string, conversationId?: string): Promise<ChatMessageResponse> {
  return request<ChatMessageResponse>("/chat/message", {
    method: "POST",
    body: JSON.stringify({ content, conversation_id: conversationId ?? null }),
  });
}

export function listConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>("/chat/conversations");
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/chat/conversations/${id}`);
}
