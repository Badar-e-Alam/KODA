from agents import Agent
from system_prompt import SYSTEM_PROMPT
from tools import (
    run_shell,
    read_file,
    write_file,
    edit_file,
    grep,
    todo_write,
    todo_update,
    think,
)

coding_agent = Agent(
    name="CodingAgent",
    instructions=SYSTEM_PROMPT,
    model="gpt-",
    tools=[
        run_shell,
        read_file,
        write_file,
        edit_file,
        grep,
        todo_write,
        todo_update,
        think,
    ],
)