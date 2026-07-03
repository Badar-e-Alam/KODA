import React, { useEffect, useReducer, useRef, useState } from "react";
import { Box, Static, Text, useApp } from "ink";
import { spawn } from "node:child_process";
import { Bridge, type BridgeOptions } from "./bridge.js";
import type { BridgeEvent, Item, PermissionItem, ToolInfo } from "./types.js";
import { transcriptReducer, nextId, type TState } from "./transcript.js";
import { getTheme, THEMES, DEFAULT_THEME } from "./theme.js";
import { nextMode, styleFor, type Mode } from "./modes.js";
import { MessageView } from "./components/Message.js";
import { Input } from "./components/Input.js";
import { Permission } from "./components/Permission.js";
import { StatusBar } from "./components/StatusBar.js";
import { TaskBar } from "./components/TaskBar.js";
import { Dashboard } from "./components/Dashboard.js";
import { AskUser } from "./components/AskUser.js";
import { SessionPicker } from "./components/SessionPicker.js";
import { setModels } from "./completer.js";
import type { SessionInfo, TaskSummary } from "./types.js";

function Spinner({ color, muted, start }: { color: string; muted: string; start: number }) {
  const [f, setF] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setF((x) => x + 1), 150);
    return () => clearInterval(t);
  }, []);
  const frames = ["·   ", "··  ", "··· ", " ···", "  ··", "   ·"];
  const el = Math.floor((Date.now() - start) / 1000);
  const ts = `${Math.floor(el / 60)}:${String(el % 60).padStart(2, "0")}`;
  return (
    <Text color={color}>
      ★ <Text color={muted} italic>{`Thinking ${ts} ${frames[f % frames.length]}`}</Text>
    </Text>
  );
}

// Cross-platform clipboard: pbcopy on macOS; wl-copy/xclip/xsel on Linux.
// Never throws — spawn "error" events are handled so a missing binary can't
// crash the app (audit finding: bare pbcopy on Linux killed the whole UI).
function copyToClipboard(text: string, done: (ok: boolean, detail?: string) => void) {
  const candidates: Array<[string, string[]]> =
    process.platform === "darwin"
      ? [["pbcopy", []]]
      : process.env.WAYLAND_DISPLAY
        ? [["wl-copy", []], ["xclip", ["-selection", "clipboard"]], ["xsel", ["--clipboard", "--input"]]]
        : [["xclip", ["-selection", "clipboard"]], ["xsel", ["--clipboard", "--input"]], ["wl-copy", []]];

  const tryNext = (i: number) => {
    if (i >= candidates.length) {
      done(false, "no clipboard tool found");
      return;
    }
    const [cmd, args] = candidates[i];
    const p = spawn(cmd, args, { stdio: ["pipe", "ignore", "ignore"] });
    let failed = false;
    p.on("error", () => {
      failed = true;
      tryNext(i + 1);
    });
    p.on("close", (code) => {
      if (!failed) done(code === 0, code === 0 ? undefined : `${cmd} exited ${code}`);
    });
    p.stdin.on("error", () => {}); // EPIPE if the tool dies mid-write
    p.stdin.write(text);
    p.stdin.end();
  };
  tryNext(0);
}

export interface AppProps {
  bridgeOptions: BridgeOptions;
  initialModel: string;
  // Launch-time resume (koda -r / -c): open the picker or continue the latest.
  startupResume?: "pick" | "latest";
}

export function App({ bridgeOptions, initialModel, startupResume }: AppProps) {
  const { exit } = useApp();
  const [tstate, dispatch] = useReducer(transcriptReducer, { committed: [], live: [] } as TState);
  const stateRef = useRef(tstate);
  stateRef.current = tstate;

  const [themeName, setThemeName] = useState(DEFAULT_THEME);
  const palette = getTheme(themeName);
  const paletteRef = useRef(palette);
  paletteRef.current = palette;

  const [model, setModel] = useState(initialModel);
  const [backend, setBackend] = useState("");
  const [mode, setMode] = useState<Mode>("default");
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [usageIn, setUsageIn] = useState(0);
  const [usageOut, setUsageOut] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const [thinkStart, setThinkStart] = useState(0);

  const [permItems, setPermItems] = useState<PermissionItem[] | null>(null);
  const [permIdx, setPermIdx] = useState(0);
  const permOutcomes = useRef<Array<"allow" | "always" | "deny">>([]);

  const [tasks, setTasks] = useState<Record<string, TaskSummary>>({});
  const [askReq, setAskReq] = useState<{ question: string; options: string[] } | null>(null);
  const [sessionList, setSessionList] = useState<SessionInfo[] | null>(null);
  const [dashboardOpen, setDashboardOpen] = useState(false);
  const [taskPerm, setTaskPerm] = useState<{ taskId: string; items: PermissionItem[] } | null>(null);
  const [taskPermIdx, setTaskPermIdx] = useState(0);
  const taskPermOutcomes = useRef<Array<"allow" | "always" | "deny">>([]);
  const rows = process.stdout.rows || 24;

  const toolsRef = useRef<ToolInfo[]>([]);
  const modelRef = useRef(model);
  modelRef.current = model;
  const bridgeRef = useRef<Bridge | null>(null);
  const didResume = useRef(false);

  function commit(item: Item) {
    dispatch({ type: "commit", item });
  }
  function info(text: string) {
    commit({ kind: "info", id: nextId(), text });
  }
  function error(text: string) {
    commit({ kind: "error", id: nextId(), text });
  }

  // ── bridge lifecycle ──────────────────────────────────────────────
  useEffect(() => {
    const bridge = new Bridge(bridgeOptions);
    bridgeRef.current = bridge;

    bridge.onEvent((ev: BridgeEvent) => {
      switch (ev.type) {
        case "ready":
          setModel(ev.model);
          setBackend(ev.backend);
          setMode((ev.mode as Mode) ?? "default");
          setTools(ev.tools);
          toolsRef.current = ev.tools;
          // koda -r / -c: once the backend is ready, either open the session
          // picker (pick) or continue the most recent session (latest). Guarded
          // so a reconnect's second "ready" doesn't re-trigger it.
          if (startupResume && !didResume.current) {
            didResume.current = true;
            bridge.send(
              startupResume === "latest"
                ? { type: "resume", session_id: "__latest__" }
                : { type: "resume" },
            );
          }
          break;
        case "text_delta":
          dispatch({ type: "text_delta", content: ev.content });
          break;
        case "thinking_delta":
          // reasoning is streamed but kept quiet in the transcript (spinner covers it)
          break;
        case "tool_start":
          if (ev.hidden) break;
          dispatch({ type: "tool_start", tool_id: ev.tool_id, name: ev.name, arguments: ev.arguments });
          break;
        case "tool_result":
          dispatch({ type: "tool_result", tool_id: ev.tool_id, output: ev.output, is_error: ev.is_error });
          break;
        case "todos":
          dispatch({ type: "todos", todos: ev.todos });
          break;
        case "usage":
          if (ev.input_tokens) setUsageIn(ev.input_tokens);
          if (ev.output_tokens) setUsageOut(ev.output_tokens);
          break;
        case "done":
          if (ev.usage) {
            if (ev.usage.input_tokens) setUsageIn(ev.usage.input_tokens);
            if (ev.usage.output_tokens) setUsageOut(ev.usage.output_tokens);
          }
          break;
        case "permission_request":
          permOutcomes.current = [];
          setPermIdx(0);
          setPermItems(ev.items);
          break;
        case "turn_end":
          dispatch({ type: "turn_end" });
          setStreaming(false);
          break;
        case "model_changed":
          setModel(ev.model);
          info(`model → ${ev.model}`);
          break;
        case "mode_changed":
          setMode(ev.mode as Mode);
          break;
        case "cleared":
          dispatch({ type: "reset" });
          info("started a new session");
          setUsageIn(0);
          setUsageOut(0);
          break;
        case "info":
          info(ev.message);
          break;
        case "error":
          error(ev.message);
          setStreaming(false);
          break;
        case "task_update":
          setTasks((prev) => ({ ...prev, [ev.task.id]: ev.task }));
          break;
        case "task_done":
          setTasks((prev) => ({ ...prev, [ev.task.id]: ev.task }));
          info(
            `subagent ${ev.task.id} (${ev.task.subagent_type}) ${ev.task.state}` +
              (ev.task.state === "success" ? ` — open /dashboard for the result` : ""),
          );
          break;
        case "task_list":
          setTasks(() => Object.fromEntries(ev.tasks.map((t) => [t.id, t])));
          break;
        case "task_permission":
          taskPermOutcomes.current = [];
          setTaskPermIdx(0);
          setTaskPerm({ taskId: ev.task_id, items: ev.items });
          break;
        case "models":
          setModels(ev.models);
          break;
        case "ask_user":
          setAskReq({ question: ev.question, options: ev.options });
          break;
        case "sessions":
          setSessionList(ev.sessions);
          break;
        case "resumed": {
          dispatch({ type: "reset" });
          for (const m of ev.messages) {
            commit(
              m.role === "user"
                ? { kind: "user", id: nextId(), text: m.content }
                : { kind: "assistant", id: nextId(), text: m.content },
            );
          }
          info(`resumed session ${ev.session_id.slice(0, 8)} — ${ev.messages.length} message(s) restored`);
          setSessionList(null);
          break;
        }
      }
    });

    bridge.onExit((code) => {
      if (code && code !== 0) {
        error(`agent process exited (code ${code}). ${bridge.stderrTail.slice(-3).join(" | ")}`);
      }
    });

    return () => bridge.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── permission answering ──────────────────────────────────────────
  function decidePermission(outcome: "allow" | "always" | "deny") {
    if (!permItems) return;
    permOutcomes.current.push(outcome);
    const next = permIdx + 1;
    if (next < permItems.length) {
      setPermIdx(next);
    } else {
      bridgeRef.current?.send({ type: "decisions", outcomes: permOutcomes.current });
      setPermItems(null);
      setPermIdx(0);
    }
  }

  function decideTaskPermission(outcome: "allow" | "always" | "deny") {
    if (!taskPerm) return;
    taskPermOutcomes.current.push(outcome);
    const next = taskPermIdx + 1;
    if (next < taskPerm.items.length) {
      setTaskPermIdx(next);
    } else {
      bridgeRef.current?.send({ type: "task_answer", task_id: taskPerm.taskId, outcomes: taskPermOutcomes.current });
      setTaskPerm(null);
      setTaskPermIdx(0);
    }
  }

  function taskControl(action: "stop" | "resume" | "restart", taskId: string) {
    const bridge = bridgeRef.current;
    if (!bridge) return;
    if (action === "stop") bridge.send({ type: "task_stop", task_id: taskId });
    else if (action === "resume") bridge.send({ type: "task_resume", task_id: taskId });
    else bridge.send({ type: "task_restart", task_id: taskId });
    info(`${action} ${taskId}`);
  }

  // ── input handling ────────────────────────────────────────────────
  function startTurn(text: string) {
    commit({ kind: "user", id: nextId(), text });
    setStreaming(true);
    setThinkStart(Date.now());
    bridgeRef.current?.send({ type: "user", text });
  }

  function runShell(raw: string) {
    const local = raw.startsWith("!!");
    const cmd = raw.slice(local ? 2 : 1).trim();
    if (!cmd) return;
    const chunks: string[] = [];
    const child = spawn("bash", ["-lc", cmd], { cwd: process.cwd() });
    child.stdout.on("data", (d) => chunks.push(d.toString()));
    child.stderr.on("data", (d) => chunks.push(d.toString()));
    child.on("close", (code) => {
      commit({
        kind: "tool",
        id: nextId(),
        toolId: "shell-" + nextId(),
        name: "shell",
        args: { command: cmd },
        output: chunks.join("") || "(no output)",
        isError: code !== 0,
        running: false,
      });
    });
    child.on("error", (e) => error(`shell: ${e.message}`));
  }

  function cycleMode() {
    const m = nextMode(mode);
    setMode(m);
    bridgeRef.current?.send({ type: "set_mode", mode: m });
    info(`mode → ${styleFor(m).label.toLowerCase()}`);
  }

  function interrupt() {
    if (streaming) {
      bridgeRef.current?.send({ type: "interrupt" });
      info("interrupting…");
    }
  }

  function quit() {
    bridgeRef.current?.close();
    exit();
  }

  function lastAssistantText(): string {
    const all = [...stateRef.current.committed, ...stateRef.current.live];
    for (let i = all.length - 1; i >= 0; i--) {
      if (all[i].kind === "assistant") return (all[i] as any).text as string;
    }
    return "";
  }

  function handleSlash(text: string) {
    const [name, ...rest] = text.slice(1).split(" ");
    const args = rest.join(" ").trim();
    const bridge = bridgeRef.current;
    switch (name.toLowerCase()) {
      case "help": {
        const lines = [
          "Slash commands:",
          "  /clear          start a new chat session",
          "  /model [m]      switch model or show current",
          "  /theme [name]   switch color theme (or list)",
          "  /plan /edits /default   switch agent mode",
          "  /compact        summarize older messages",
          "  /copy           copy last assistant reply",
          "  /usage          show token usage",
          "  /agents /tools  describe the agent / list tools",
          "  /tasks          list background subagents",
          "  /dashboard      full-screen subagent manager",
          "  /tree [id]      show the session tree · jump to a node to branch",
          "  /resume [id]    pick a past session and continue it",
          "  /skill [new …]  list skills, or author one with the current model",
          "  /setup          configure API keys (see message)",
          "  /help /quit /exit",
          "",
          "Input: @path attaches a file · !cmd runs shell · Shift+Tab cycles mode",
        ];
        info(lines.join("\n"));
        break;
      }
      case "theme":
        if (!args) {
          info("Themes: " + Object.keys(THEMES).sort().join(", "));
        } else if (THEMES[args]) {
          setThemeName(args);
          info(`theme → ${args}`);
        } else {
          error(`unknown theme: ${args}`);
        }
        break;
      case "clear":
        bridge?.send({ type: "clear" });
        break;
      case "quit":
      case "exit":
        quit();
        break;
      case "copy": {
        const t = lastAssistantText();
        if (!t) {
          info("nothing to copy yet");
          break;
        }
        copyToClipboard(t, (ok, detail) =>
          ok ? info(`copied ${t.length} chars`) : error(`clipboard unavailable${detail ? ` (${detail})` : ""}`),
        );
        break;
      }
      case "usage":
        info(`Session usage — input: ${usageIn.toLocaleString()}  output: ${usageOut.toLocaleString()}`);
        break;
      case "model":
        // No args → ask the bridge (authoritative even before/after switches,
        // and avoids echoing the pre-ready "…" placeholder).
        bridge?.send({ type: "switch_model", model: args });
        break;
      case "plan":
      case "edits":
      case "default": {
        const m = (name.toLowerCase() as Mode);
        setMode(m);
        bridge?.send({ type: "set_mode", mode: m });
        info(`mode → ${styleFor(m).label.toLowerCase()}`);
        break;
      }
      case "compact":
        bridge?.send({ type: "compact" });
        break;
      case "agents":
        info(
          `Agent — backend: ${backend}  model: ${modelRef.current}  tools: ${toolsRef.current.length}`,
        );
        break;
      case "tools": {
        const names = toolsRef.current.map((t) => t.name);
        info(`Tools (${names.length}):\n  ` + names.join(", "));
        break;
      }
      case "tree":
        // No arg → render the branchable session tree; with a node id → jump
        // there (the next message branches from that node).
        bridge?.send({ type: "tree", node: args || undefined });
        break;
      case "resume":
        // No arg → picker of this project's past sessions; with an id →
        // resume it directly (appends to the same session file).
        bridge?.send({ type: "resume", session_id: args || undefined });
        break;
      case "skill":
      case "skills": {
        // No arg → list skills. `/skill new <name>: <what>` (or just
        // `/skill <brief>`) → author one with the configured model & save it.
        const brief = args.replace(/^new\s+/i, "").trim();
        if (!brief) bridge?.send({ type: "skill", action: "list" });
        else bridge?.send({ type: "skill", action: "create", brief });
        break;
      }
      case "tasks": {
        const all = Object.values(tasks);
        if (all.length === 0) {
          info("No background subagents. The agent starts them with start_async_task — ask it to run something in the background.");
        } else {
          info(
            "Background subagents:\n" +
              all
                .map((t) => `  ${t.id} [${t.state}] ${t.subagent_type} — ${t.current} · ${t.tool_count} tools\n    ${t.description.slice(0, 70)}`)
                .join("\n") +
              "\n(/dashboard to manage)",
          );
        }
        break;
      }
      case "dashboard":
        setDashboardOpen(true);
        break;
      case "setup":
        info(
          "Set provider keys in your environment or .env (ANTHROPIC_API_KEY, " +
            "OPENAI_API_KEY, GOOGLE_API_KEY, OLLAMA_API_KEY, …), then /model <provider:model>. " +
            "For Ollama Cloud add OLLAMA_USE_CLOUD=1.",
        );
        break;
      default:
        error(`unknown command: /${name}`);
    }
  }

  function onSubmit(text: string) {
    if (text.startsWith("!")) {
      runShell(text);
      return;
    }
    if (text.startsWith("/")) {
      handleSlash(text);
      return;
    }
    startTurn(text);
  }

  // ── render ────────────────────────────────────────────────────────
  const taskList = Object.values(tasks).sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));

  // Full-screen-on-demand: the dashboard takes over the view while open.
  if (dashboardOpen) {
    return (
      <Dashboard
        tasks={taskList}
        palette={palette}
        rows={rows}
        onClose={() => setDashboardOpen(false)}
        onControl={taskControl}
      />
    );
  }

  const permActive = !!permItems;
  const taskPermActive = !permActive && !!taskPerm;
  const askActive = !permActive && !taskPermActive && !!askReq;
  const pickerActive = !permActive && !taskPermActive && !askActive && !!sessionList;
  const inputActive = !permActive && !taskPermActive && !askActive && !pickerActive;

  return (
    <Box flexDirection="column">
      <Static items={tstate.committed}>
        {(item) => <MessageView key={item.id} item={item} palette={palette} />}
      </Static>

      <Box flexDirection="column">
        {tstate.live.map((item) => (
          <MessageView key={item.id} item={item} palette={palette} />
        ))}

        {streaming && !permActive && !taskPermActive ? (
          <Box marginTop={1}>
            <Spinner color={palette.accent} muted={palette.muted} start={thinkStart} />
          </Box>
        ) : null}

        {permActive && permItems ? (
          <Permission
            key={`perm-${permIdx}`}
            item={permItems[permIdx]}
            index={permIdx}
            total={permItems.length}
            palette={palette}
            onDecide={decidePermission}
          />
        ) : null}

        {taskPermActive && taskPerm ? (
          <Box flexDirection="column" marginTop={1}>
            <Text color={palette.accent}>Background task {taskPerm.taskId} needs approval:</Text>
            <Permission
              key={`taskperm-${taskPermIdx}`}
              item={taskPerm.items[taskPermIdx]}
              index={taskPermIdx}
              total={taskPerm.items.length}
              palette={palette}
              onDecide={decideTaskPermission}
            />
          </Box>
        ) : null}

        {askActive && askReq ? (
          <AskUser
            question={askReq.question}
            options={askReq.options}
            palette={palette}
            onAnswer={(value) => {
              bridgeRef.current?.send({ type: "ask_answer", value });
              commit({ kind: "info", id: nextId(), text: `answered: ${value}` });
              setAskReq(null);
            }}
          />
        ) : null}

        {pickerActive && sessionList ? (
          <SessionPicker
            sessions={sessionList}
            palette={palette}
            onPick={(id) => bridgeRef.current?.send({ type: "resume", session_id: id })}
            onClose={() => setSessionList(null)}
          />
        ) : null}

        <Box marginTop={1} flexDirection="column">
          <Input
            active={inputActive}
            palette={palette}
            mode={mode}
            streaming={streaming}
            onSubmit={onSubmit}
            onInterrupt={interrupt}
            onCycleMode={cycleMode}
            onExit={quit}
          />
          <TaskBar tasks={taskList} palette={palette} />
          <StatusBar model={model} mode={mode} inputTokens={usageIn} outputTokens={usageOut} palette={palette} />
        </Box>
      </Box>
    </Box>
  );
}
