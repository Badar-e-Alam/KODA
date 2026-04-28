from agents import Agent, Runner, set_tracing_disabled
from dotenv import load_dotenv

set_tracing_disabled(True)

from subagent import dispatch_subagent
from system_prompt import SYSTEM_PROMPT
from tools import (
    WORKDIR,
    edit_file,
    find_files,
    grep,
    multi_edit,
    read_file,
    run_shell,
    think,
    todo_update,
    todo_write,
    write_file,
)

load_dotenv("/home/b.alam/KODA/.env")


coding_agent = Agent(
    name="Coding agent",
    instructions=(
        f"{SYSTEM_PROMPT}\n\nWorking directory: {WORKDIR}\n"
        "Resolve relative paths (e.g. 'clients.py') against this directory — "
        "you do not need to ask the user for full paths."
    ),
    model="gpt-4o",
    tools=[
        run_shell,
        read_file,
        write_file,
        edit_file,
        multi_edit,
        find_files,
        grep,
        dispatch_subagent,
        todo_write,
        todo_update,
        think,
    ],
)


if __name__ == "__main__":
    result = Runner.run_sync(coding_agent, "List the files in the current directory.")
    print(result.final_output)
