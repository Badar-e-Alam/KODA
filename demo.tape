# KODA Demo — terminal GIF
# Run:  vhs demo.tape
# Deps: ffmpeg, koda (pip install -e .)

Output assets/koda_demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Set CursorBlink false

# ── Boot: activate venv (hidden — fast) ─────────────────────────

Hide
Type "cd ~/projects/KODA && source .venv/Scripts/activate"
Enter
Sleep 2s
Show

# ── Scene 1: Show the CLI help ──────────────────────────────────

Sleep 500ms
Type "# KODA — Open-source AI coding agent in your terminal"
Enter
Sleep 1.5s

Type "koda --help"
Enter
Sleep 4s

# ── Scene 2: Show model options ─────────────────────────────────

Type "# Supports multiple providers:"
Enter
Sleep 500ms
Type "# koda -m anthropic:claude-sonnet-4-6"
Enter
Type "# koda -m openai:gpt-4o"
Enter
Type "# koda -m google:gemini-2.5-flash"
Enter
Type "# koda -m ollama:llama3.1"
Enter
Sleep 3s

# ── Scene 3: Launch KODA TUI ────────────────────────────────────

Type "koda -y"
Enter

# Wait for the TUI banner to fully render
Sleep 10s

# ── Scene 4: Ask about Bitcoin price ────────────────────────────

Type "What is the current Bitcoin price today?"
Sleep 1s
Enter

# Wait for agent: tool calls + LLM response
Sleep 50s

# Let viewer read the result
Sleep 5s
