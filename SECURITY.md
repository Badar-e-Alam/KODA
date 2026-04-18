# Security Policy

## Reporting a vulnerability

If you believe you've found a security issue in KODA, please **do not** open a
public GitHub issue. Instead, email the maintainer (see the `authors` field in
`pyproject.toml`) with:

- A description of the issue
- Steps to reproduce
- Any suggested fix

We'll acknowledge within 7 days and aim to have a patch or mitigation within
30 days for confirmed issues.

## Threat model

KODA runs a tool-calling LLM with:

- **Filesystem tools** (`read_file`, `write_file`, `edit_file`, `ls`, `glob`,
  `grep`) jailed to a workspace directory (`./agent_workspace/` by default,
  override with `KODA_WORKSPACE`). Symlinks inside the jail are rejected to
  prevent escape.
- **A shell tool** (`execute`) that runs `subprocess.run(shell=True)` inside
  the workspace directory. This is **not sandboxed** at the OS level — it can
  read any file the process user can read, write anywhere `cwd` allows, make
  network requests, and install packages. The same is true of the user-facing
  `!shell` command in the TUI.
- **Web tools** (`web_search`, `read_webpage`) that proxy through
  [Jina AI](https://jina.ai). `read_webpage` validates the target URL is an
  http/https scheme and resolves to a public address (private/loopback/link-
  local IPs are rejected).

### What KODA does NOT protect against

- A malicious or mistaken LLM running a destructive shell command. Users
  accepting arbitrary agent output should expect this risk.
- Prompt injection through files the agent reads (e.g. a README telling the
  agent to exfiltrate secrets). Run KODA in projects you trust.
- Supply-chain compromise of LLM providers or Python dependencies.

### Hardening recommendations

- Run KODA in a container or VM when working in untrusted repositories.
- Set `KODA_WORKSPACE` to a dedicated directory with no sensitive files.
- Do not mount `~` or `/etc` into the workspace.
- Use a dedicated API key for each provider so you can revoke it.
- Review shell commands the agent proposes before approving (auto-approve via
  `-y` is a deliberate convenience, not a safe default).

## Secrets

KODA reads API keys from environment variables (and `.env` via python-dotenv).
`.env` is gitignored. Never commit `.env` or provider keys.
