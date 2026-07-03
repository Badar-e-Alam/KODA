// Wire protocol shared with koda/bridge.py (NDJSON over stdio).

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
}

export interface ToolInfo {
  name: string;
  description: string;
}

export interface PermissionItem {
  tool_name: string;
  args: Record<string, unknown>;
  allowed_decisions: string[];
  description: string;
  /** Real on-disk target when the tool's path is workspace-jailed. */
  resolved_path?: string;
}

// Status vocabulary matches deepagents' official async-subagents middleware
// ("running" | "success" | "error" | "cancelled"); "queued"/"paused" are
// KODA extensions (paused = awaiting a permission decision).
export interface TaskSummary {
  id: string;
  description: string;
  subagent_type: string;
  state: "queued" | "running" | "paused" | "success" | "error" | "cancelled";
  tool_count: number;
  current: string;
  reply_chars: number;
  elapsed: number;
  error: string;
  awaiting_permission: boolean;
  input_tokens?: number;
  output_tokens?: number;
  /** Peek inside the agent: its most recent tool calls (up to 10). */
  recent_tools?: string[];
  /** Tail of the agent's output so far (up to 400 chars). */
  preview?: string;
  /** Chronological log of what the agent did — one line per tool call. */
  activity?: string[];
}

// Bridge → client events.
export type BridgeEvent =
  | { type: "ready"; model: string; backend: string; cwd: string; mode: string; supports_thinking: boolean; supports_vision: boolean; tools: ToolInfo[] }
  | { type: "text_delta"; content: string }
  | { type: "thinking_delta"; content: string }
  | { type: "tool_start"; tool_id: string; name: string; arguments: Record<string, unknown>; hidden?: boolean }
  | { type: "tool_result"; tool_id: string; output: string; is_error: boolean }
  | { type: "todos"; todos: Todo[] }
  | { type: "usage"; input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number }
  | { type: "permission_request"; items: PermissionItem[] }
  | { type: "done"; usage: Usage | null }
  | { type: "turn_end"; reply: string }
  | { type: "model_changed"; model: string }
  | { type: "mode_changed"; mode: string }
  | { type: "cleared" }
  | { type: "info"; message: string }
  | { type: "error"; message: string }
  | { type: "task_update"; task: TaskSummary }
  | { type: "task_done"; task: TaskSummary; result: string }
  | { type: "task_list"; tasks: TaskSummary[] }
  | { type: "task_permission"; task_id: string; items: PermissionItem[] }
  | { type: "models"; models: string[] }
  | { type: "ask_user"; question: string; options: string[] }
  | { type: "sessions"; sessions: SessionInfo[] }
  | { type: "resumed"; session_id: string; messages: Array<{ role: string; content: string }> };

export interface SessionInfo {
  id: string;
  started: string;
  messages: number;
  preview: string;
}

export interface Todo {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

// Client → bridge commands.
export type BridgeCommand =
  | { type: "user"; text: string }
  | { type: "interrupt" }
  | { type: "decisions"; outcomes: Array<"allow" | "always" | "deny"> }
  | { type: "set_mode"; mode: string }
  | { type: "switch_model"; model: string }
  | { type: "compact" }
  | { type: "clear" }
  | { type: "describe" }
  | { type: "task_stop"; task_id: string }
  | { type: "task_resume"; task_id: string; message?: string }
  | { type: "task_restart"; task_id: string }
  | { type: "task_answer"; task_id: string; outcomes: Array<"allow" | "always" | "deny"> }
  | { type: "task_list" }
  | { type: "ask_answer"; value: string }
  | { type: "tree"; node?: string }
  | { type: "resume"; session_id?: string }
  | { type: "skill"; action: "list" | "create"; brief?: string }
  | { type: "quit" };

// ── Transcript items (what the UI renders) ──────────────────────────────

export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "tool"; id: string; toolId: string; name: string; args: Record<string, unknown>; output?: string; isError?: boolean; running: boolean }
  | { kind: "todos"; id: string; todos: Todo[] }
  | { kind: "info"; id: string; text: string }
  | { kind: "error"; id: string; text: string };
