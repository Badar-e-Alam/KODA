from agents import Runner
from agent import coding_agent
from dotenv import load_dotenv

config=load_dotenv("/home/b.alam/KODA/.env")

def main():
    history = []
    print("coding agent ready. type 'exit' to quit.\n")
    while True:
        user = input("you> ").strip()
        if user in {"", "exit", "quit"}:
            break

        history.append({"role": "user", "content": user})
        result = Runner.run_sync(coding_agent, history)
        history = result.to_input_list()  # carries tool calls + results forward

        print(f"\nagent> {result.final_output}\n")


if __name__ == "__main__":
    main()