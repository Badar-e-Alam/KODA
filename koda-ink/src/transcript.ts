// Block-commit model. `live` holds only what is still in flight; finished
// blocks flush (in chronological order) into `committed`, which the UI renders
// in an Ink <Static> so they land permanently in scrollback.
//
// The load-bearing invariant: the live (non-<Static>) region must stay SHORT.
// Ink switches to a full-screen clear+repaint (erasing scrollback — the
// "terminal within a terminal" effect) the instant the live region's height
// reaches the terminal's row count (ink.js:121). So we:
//   • commit finished tool/assistant/todos/info blocks to <Static> immediately;
//   • never keep more than the *current* streaming assistant paragraph live,
//     force-committing older lines once it exceeds LIVE_ASSIST_CAP;
//   • insert out-of-turn commits (user/info/error) behind still-live blocks so
//     they can't jump ABOVE the reply that is still streaming.

import type { Item, Todo } from "./types.js";

export type LiveItem = Item & { final?: boolean };

export interface TState {
  committed: Item[];
  live: LiveItem[];
}

// Max lines of streaming assistant text kept in the live region before older
// lines are force-committed. Small enough that live + spinner + input + status
// stays well under any realistic terminal height.
const LIVE_ASSIST_CAP = 5;

let counter = 0;
export function nextId(): string {
  counter += 1;
  return "i" + counter;
}

export type TAction =
  | { type: "text_delta"; content: string }
  | { type: "tool_start"; tool_id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; tool_id: string; output: string; is_error: boolean }
  | { type: "todos"; todos: Todo[] }
  | { type: "turn_end" }
  | { type: "commit"; item: Item } // an out-of-turn item (user msg, info, error, shell result)
  | { type: "reset" };

function flushFinalPrefix(state: TState): TState {
  let { committed, live } = state;
  let moved = false;
  while (live.length && live[0].final) {
    if (!moved) {
      committed = committed.slice();
      live = live.slice();
      moved = true;
    }
    const { final, ...item } = live.shift() as LiveItem;
    committed.push(item as Item);
  }
  return moved ? { committed, live } : state;
}

// Finalize the trailing "soft" block (streaming assistant or in-place todos) so
// it can flush. Running tools are left alone — they finalize on their result.
function finalizeSoftTail(live: LiveItem[]): LiveItem[] {
  if (!live.length) return live;
  const i = live.length - 1;
  const t = live[i];
  if ((t.kind === "assistant" || t.kind === "todos") && !t.final) {
    const copy = live.slice();
    copy[i] = { ...t, final: true };
    return copy;
  }
  return live;
}

// Split a streaming assistant buffer into [completed chunks, remaining tail].
// Commits whole paragraphs eagerly, and force-commits by line once the tail
// grows past LIVE_ASSIST_CAP so the live region can never get tall.
function drainAssistant(buf: string): [string[], string] {
  const chunks: string[] = [];
  for (let guard = 0; guard < 4096; guard++) {
    const lines = buf.split("\n");
    if (lines.length > LIVE_ASSIST_CAP) {
      const keep = LIVE_ASSIST_CAP - 1;
      const commit = lines.slice(0, lines.length - keep).join("\n").replace(/\s+$/, "");
      buf = lines.slice(lines.length - keep).join("\n");
      if (commit !== "") chunks.push(commit);
      continue;
    }
    const para = buf.indexOf("\n\n");
    if (para >= 0) {
      const commit = buf.slice(0, para);
      buf = buf.slice(para + 2);
      if (commit.trim() !== "") chunks.push(commit);
      continue;
    }
    break;
  }
  return [chunks, buf];
}

export function transcriptReducer(state: TState, action: TAction): TState {
  switch (action.type) {
    case "reset":
      return { committed: [], live: [] };

    case "commit": {
      // Park it behind any still-live blocks (as a final item) so it commits in
      // chronological order rather than jumping above a streaming reply.
      const live = [...state.live, { ...action.item, final: true } as LiveItem];
      return flushFinalPrefix({ ...state, live });
    }

    case "text_delta": {
      // Append to the still-open assistant block if one exists anywhere in live
      // (it may not be the tail — a queued user msg / info can sit after it), so
      // a reply that resumes after a mid-stream commit stays one contiguous block.
      let openIdx = -1;
      for (let i = state.live.length - 1; i >= 0; i--) {
        if (state.live[i].kind === "assistant" && !state.live[i].final) {
          openIdx = i;
          break;
        }
      }
      let live: LiveItem[];
      if (openIdx < 0) {
        live = [...finalizeSoftTail(state.live), { kind: "assistant", id: nextId(), text: "", final: false }];
        openIdx = live.length - 1;
      } else {
        live = state.live.slice();
      }
      const la = live[openIdx] as { id: string; text: string };
      const [chunks, remaining] = drainAssistant(la.text + action.content);
      live[openIdx] = { kind: "assistant", id: la.id, text: remaining, final: false };
      if (chunks.length) {
        const finals: LiveItem[] = chunks.map((c) => ({ kind: "assistant", id: nextId(), text: c, final: true }));
        live.splice(openIdx, 0, ...finals);
      }
      return flushFinalPrefix({ ...state, live });
    }

    case "tool_start": {
      const live = [
        ...finalizeSoftTail(state.live),
        {
          kind: "tool" as const,
          id: nextId(),
          toolId: action.tool_id,
          name: action.name,
          args: action.arguments,
          running: true,
          final: false,
        },
      ];
      return flushFinalPrefix({ ...state, live });
    }

    case "tool_result": {
      const found = state.live.some((it) => it.kind === "tool" && it.toolId === action.tool_id);
      if (!found) {
        // Orphan result — the adapter fabricates one (tool_id "adapter_error")
        // for graph-level failures (model down, auth, rate limit). Surface it
        // as an error block instead of silently dropping it.
        if (action.is_error) {
          const live = [
            ...state.live,
            { kind: "error" as const, id: nextId(), text: humanizeError(action.output), final: true },
          ];
          return flushFinalPrefix({ ...state, live });
        }
        return state;
      }
      const live = state.live.map((it) =>
        it.kind === "tool" && it.toolId === action.tool_id
          ? { ...it, output: action.output, isError: action.is_error, running: false, final: true }
          : it,
      );
      return flushFinalPrefix({ ...state, live });
    }

    case "todos": {
      const last = state.live[state.live.length - 1];
      if (last && last.kind === "todos" && !last.final) {
        const live = state.live.slice();
        live[live.length - 1] = { ...last, todos: action.todos };
        return { ...state, live };
      }
      const live = [
        ...finalizeSoftTail(state.live),
        { kind: "todos" as const, id: nextId(), todos: action.todos, final: false },
      ];
      return flushFinalPrefix({ ...state, live });
    }

    case "turn_end": {
      const live = state.live.map((it) => ({ ...it, final: true }));
      return flushFinalPrefix({ ...state, live });
    }
  }
}

const HINTS: Array<[string, string]> = [
  ["connecterror", "Model server unreachable. If using ollama, run `ollama serve`."],
  ["connection refused", "Model server refused the connection. Is it running?"],
  ["all connection attempts failed", "Could not reach the model server. Check host/port."],
  ["401", "Authentication failed. Check your API key."],
  ["unauthorized", "Authentication failed. Check your API key."],
  ["403", "Access denied. The API key may lack permission for this model."],
  ["429", "Rate-limited by the provider. Wait a moment and retry."],
  ["timeout", "Request timed out. The model server may be slow or overloaded."],
];

function humanizeError(raw: string): string {
  const low = (raw || "").toLowerCase();
  for (const [needle, hint] of HINTS) {
    if (low.includes(needle)) {
      const head = raw.split(":").pop()?.trim().slice(0, 120) ?? "";
      return `${hint}  (${head})`;
    }
  }
  return raw.slice(0, 240);
}
