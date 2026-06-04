LOG_LEVEL = "INFO"

def debug(msg):
    if LOG_LEVEL == "DEBUG":
        print(f"[DEBUG] {msg}")

def info(msg):
    if LOG_LEVEL in ("DEBUG", "INFO"):
        print(f"[INFO] {msg}")
