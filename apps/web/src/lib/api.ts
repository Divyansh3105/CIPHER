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

export interface Citation {
  kind: "document" | "web";
  // document
  document_id?: string | null;
  filename?: string | null;
  page_number?: number | null;
  content?: string | null;
  similarity?: number | null;
  // web
  title?: string | null;
  url?: string | null;
  snippet?: string | null;
  provider?: string | null;
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
  // Phase 5: what grounded this reply -- document passages or web results.
  // Same snapshot reasoning: a citation must keep saying what the answer was
  // based on, even after the document is deleted.
  citations: Citation[];
}

export interface ChatMessageResponse {
  conversation_id: string;
  message: ChatMessage;
  model_used: string;
  fell_back: boolean;
  // True when the persona's output filter (ULTRON only, today) replaced the
  // model's reply with a safety refusal.
  filtered: boolean;
  // Phase 6. null means nothing contributed -- INCLUDING when a specialist
  // was chosen and failed, in which case `activity` says so. "Answered
  // directly" and "tried a specialist and it did not work" deserve different
  // amounts of trust, so the UI has to be able to tell them apart.
  agent_used: string | null;
  activity: string;
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

// Returns counts rather than void, and the counts are worth surfacing: the
// backend detaches agent runs instead of letting them cascade away, so
// "deleted 1, kept 3 runs" is a different outcome from "deleted 1".
export function deleteConversation(
  id: string
): Promise<{ deleted: number; messages_removed: number; agent_runs_detached: number }> {
  return request<{ deleted: number; messages_removed: number; agent_runs_detached: number }>(
    `/chat/conversations/${id}`,
    { method: "DELETE" }
  );
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

// --- Phase 4: runtime model swap -------------------------------------

export interface ModelInfo {
  id: string;
  provider: string;
  display_name: string;
  aliases: string[];
  note: string;
}

export interface ActiveModel {
  // False means default routing (primary with automatic fallback). True
  // means a model was explicitly named and will NOT silently fall back.
  pinned: boolean;
  id: string;
  provider: string;
  display_name: string;
  default_id: string;
}

export interface ModelsResponse {
  active: ActiveModel;
  available: ModelInfo[];
}

export function listModels(): Promise<ModelsResponse> {
  return request<ModelsResponse>("/models");
}

// Takes what the user said or picked, not an id -- resolution and refusal
// both live on the backend (services/backend/app/llm/registry.py), so there
// is exactly one place that decides what a model name means. A 404 here is
// the refusal, and its `detail` lists the models that do exist.
export function setActiveModel(spoken: string): Promise<ActiveModel> {
  return request<ActiveModel>("/models/active", {
    method: "POST",
    body: JSON.stringify({ spoken }),
  });
}

export function clearActiveModel(): Promise<ActiveModel> {
  return request<ActiveModel>("/models/active", { method: "DELETE" });
}

// --- Phase 4: memory graph -------------------------------------------

export interface MemoryGraphNode {
  id: string;
  content: string;
  memory_type: MemoryType;
  source: string;
  persona: Persona | null;
  created_at: string;
  last_recalled_at: string | null;
  // Stored but not yet searchable. Such a node is always isolated, and the
  // galaxy says why rather than leaving it an unexplained loner.
  embedding_pending: boolean;
}

export interface MemoryGraphLink {
  source: string;
  target: string;
  similarity: number;
}

export interface MemoryGraph {
  nodes: MemoryGraphNode[];
  links: MemoryGraphLink[];
  // Total memories before the node cap, so the view can admit to showing a
  // subset instead of quietly drawing a partial picture.
  total: number;
  truncated: boolean;
  // The floor actually applied. `adaptive` means it was derived from this
  // store's own distribution rather than requested -- a fixed constant does
  // not survive real embeddings, see MEMORY_GRAPH_SIGMA in the backend.
  min_similarity: number;
  adaptive: boolean;
  neighbours: number;
}

export function getMemoryGraph(options?: {
  neighbours?: number;
  minSimilarity?: number | null;
}): Promise<MemoryGraph> {
  const params = new URLSearchParams();
  if (options?.neighbours != null) params.set("neighbours", String(options.neighbours));
  // null/undefined deliberately omits the parameter, which is what asks the
  // backend to derive the floor instead of imposing one.
  if (options?.minSimilarity != null) params.set("min_similarity", String(options.minSimilarity));
  const query = params.toString();
  return request<MemoryGraph>(`/memory/graph${query ? `?${query}` : ""}`);
}

// --- Phase 5: documents, tools, citations ----------------------------

export type DocumentStatus = "pending" | "ready" | "failed";

export interface DocumentRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  // Populated when status is "failed", and written for a person to read.
  // A document that silently never becomes searchable is the worst outcome,
  // so this is always shown rather than logged.
  error: string | null;
  chunk_count: number;
  page_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentUploadResult {
  document: DocumentRecord;
  // True when the same text was already uploaded. Not an error: the backend
  // returns 201 and points at the existing document.
  deduplicated: boolean;
}

export interface DocumentChunk {
  id: string;
  chunk_index: number;
  page_number: number | null;
  content: string;
  // False when the passage was stored but never embedded -- it is in the
  // document and can never be retrieved. Shown rather than implied: a panel
  // that lists it as an ordinary passage lies about what CIPHER can quote.
  embedded: boolean;
}

export interface ToolInfo {
  name: string;
  description: string;
  requires_permission: boolean;
  // Whether it can run right now, and why not if it cannot.
  available: boolean;
  reason: string;
}

export function listDocuments(): Promise<DocumentRecord[]> {
  return request<DocumentRecord[]>("/documents");
}

export function deleteDocument(id: string): Promise<{ deleted: number; chunks_removed: number }> {
  return request<{ deleted: number; chunks_removed: number }>(`/documents/${id}`, {
    method: "DELETE",
  });
}

// The passages a document was split into, in document order. This is what
// makes a citation checkable: "handbook.pdf, page 4" points at a passage you
// can actually read here, and page numbers are true because chunks never
// span pages.
export function listDocumentChunks(id: string): Promise<DocumentChunk[]> {
  return request<DocumentChunk[]>(`/documents/${id}/chunks`);
}

export function listTools(): Promise<ToolInfo[]> {
  return request<ToolInfo[]>("/tools");
}

// Not routed through request<T>(): that sets Content-Type: application/json,
// and a multipart upload needs the browser to set its own boundary. Setting
// it by hand produces a 422 that looks like a validation bug.
export async function uploadDocument(file: File): Promise<DocumentUploadResult> {
  const form = new FormData();
  form.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/documents`, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, `Could not reach the backend. Is it running on ${API_BASE_URL}?`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<DocumentUploadResult>;
}

// --- Phase 6: agents -------------------------------------------------

export type AgentRunStatus = "ok" | "failed" | "timeout" | "skipped";

export interface AgentInfo {
  name: string;
  description: string;
  timeout_seconds: number;
  // False means switched off; the router is not offered it at all.
  enabled: boolean;
}

export interface AgentRun {
  id: string;
  agent_name: string;
  conversation_id: string | null;
  input: string;
  output: string | null;
  status: AgentRunStatus;
  error: string | null;
  duration_ms: number;
  created_at: string;
}

export function listAgents(): Promise<AgentInfo[]> {
  return request<AgentInfo[]>("/agents");
}

export function setAgentEnabled(name: string, enabled: boolean): Promise<AgentInfo> {
  return request<AgentInfo>(`/agents/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

// Failed, timed-out and did-nothing runs come back alongside successful
// ones. An activity view that lists only what worked lies by omission, and
// "why was that answer ungrounded" is the question this exists to answer.
export function listAgentRuns(conversationId?: string): Promise<AgentRun[]> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  return request<AgentRun[]>(`/agents/runs${query}`);
}

// --- Phase 7: automation and vision ----------------------------------

export type RiskTier = "read_only" | "session" | "sensitive";
export type PermissionLevel = "session" | "trusted";
export type ActivityOutcome = "approved" | "denied" | "executed" | "failed" | "blocked";

export interface ActionInfo {
  name: string;
  description: string;
  category: string;
  risk: RiskTier;
  // Whether it could run right now with no further approval.
  allowed_now: boolean;
  // Empty when allowed_now. Says what is missing, so the UI can offer the
  // fix rather than only reporting a wall.
  reason: string;
  // True for every sensitive action, always — a session approval never
  // silently covers those.
  needs_confirmation: boolean;
}

export interface AutomationStatus {
  // False when AUTOMATION_ENABLED is unset. Nothing runs in that state.
  enabled: boolean;
  kill_switch_engaged: boolean;
  actions: ActionInfo[];
  notice: string;
}

export interface ActivityLogEntry {
  id: string;
  action_name: string;
  category: string;
  risk: RiskTier;
  persona: string | null;
  arguments: Record<string, unknown>;
  outcome: ActivityOutcome;
  reason: string | null;
  result: string | null;
  created_at: string;
}

export interface AutomationExecuteResult {
  action_name: string;
  outcome: string;
  summary: string;
  detail: string;
}

export function getAutomationStatus(): Promise<AutomationStatus> {
  return request<AutomationStatus>("/automation/actions");
}

export function approveAction(actionName: string, level: PermissionLevel = "session"): Promise<AutomationStatus> {
  return request<AutomationStatus>("/automation/permissions", {
    method: "POST",
    body: JSON.stringify({ action_name: actionName, level }),
  });
}

export function revokeAction(actionName: string): Promise<AutomationStatus> {
  return request<AutomationStatus>(`/automation/permissions/${encodeURIComponent(actionName)}`, {
    method: "DELETE",
  });
}

export function engageKillSwitch(): Promise<AutomationStatus> {
  return request<AutomationStatus>("/automation/stop", { method: "POST" });
}

export function releaseKillSwitch(): Promise<AutomationStatus> {
  return request<AutomationStatus>("/automation/resume", { method: "POST" });
}

export function listActivityLog(): Promise<ActivityLogEntry[]> {
  return request<ActivityLogEntry[]>("/automation/log");
}

// `confirmed` is the per-action confirmation. It is required for every
// sensitive action, every time, regardless of any standing approval — so it
// is a parameter here rather than something the client can forget.
export function executeAction(
  actionName: string,
  args: Record<string, unknown>,
  options?: { confirmed?: boolean; persona?: string }
): Promise<AutomationExecuteResult> {
  return request<AutomationExecuteResult>("/automation/execute", {
    method: "POST",
    body: JSON.stringify({
      action_name: actionName,
      arguments: args,
      confirmed: options?.confirmed ?? false,
      persona: options?.persona ?? null,
    }),
  });
}

export interface VisionResult {
  answer: string;
  model_used: string;
}

// Multipart, so it bypasses request<T>() for the same reason uploadDocument
// does: the browser must set its own boundary.
export async function askAboutImage(
  blob: Blob,
  question: string,
  persona: string
): Promise<VisionResult> {
  const form = new FormData();
  form.append("image", blob, "frame.jpg");
  form.append("question", question);
  form.append("persona", persona);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/vision`, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, `Could not reach the backend. Is it running on ${API_BASE_URL}?`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<VisionResult>;
}

// --- Speech to text without the browser's speech service --------------

// Posts one recorded utterance and returns what was said. Multipart, so it
// bypasses request<T>() for the same reason uploadDocument does.
//
// The filename matters: it carries the container format, which is how the
// transcription service knows how to decode the audio. Passing a generic
// name makes valid audio look corrupt.
export async function transcribe(blob: Blob, language?: string): Promise<string> {
  const extension = blob.type.includes("ogg")
    ? "ogg"
    : blob.type.includes("mp4")
      ? "mp4"
      : blob.type.includes("wav")
        ? "wav"
        : "webm";

  const form = new FormData();
  form.append("audio", blob, `utterance.${extension}`);
  if (language) form.append("language", language);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/voice/transcribe`, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, `Could not reach the backend. Is it running on ${API_BASE_URL}?`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }
  return ((await response.json()) as { text: string }).text;
}
