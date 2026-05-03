# task_11_long_session — solution hint

This task exists to **exercise context compaction** rather than test a
specific coding skill. The 20 module files in `api/` collectively add up
to ~40 KB of source. Each `read_file` call brings ~2 KB into the
agent's running history; by file 13–15 the total context crosses the
default 50 KB compaction threshold and the agent's older history gets
folded into a summary system message.

A correct solution:
1. Iterates over every file in `api/` (in any order).
2. Reads it with `read_file`.
3. Extracts the resource name and downstream service from the docstring.
4. After all reads, writes `audit.md` with 20 bullets, one per file.

The compaction code path is exercised whenever the agent reads enough
files to cross the threshold mid-task. If compaction misbehaves
(corrupts message ordering, breaks the assistant↔tool pairing, or
causes the agent to forget the task), this task will fail.

Look for the log line `compacted N msgs (~M chars) into 1 summary msg`
in the eval traces to confirm compaction actually fired.
