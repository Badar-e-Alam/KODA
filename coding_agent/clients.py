from agents import Agent
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

class ModelInterface:
    """Interface for integrating different models."""
    def execute(self, prompt: str) -> str:
        raise NotImplementedError("This method should be overridden by subclasses.")

class CodingAgent:
    def __init__(self, name: str, instructions: str, model: ModelInterface, tools: list):
        self.agent = Agent(name, instructions, tools)
        self.model = model

if __name__ == "__main__":
    # OpenAI model implementation
    class OpenAIModel(ModelInterface):
        def execute(self, prompt: str) -> str:
            # Implement connection using OpenAI SDK
            return "OpenAI response"

    # Anthropic model implementation
    class AnthropicModel(ModelInterface):
        def execute(self, prompt: str) -> str:
            # Implement connection for Anthropic
            return "Anthropic response"

    # Generic model using OpenAI SDK
    class GenericModel(ModelInterface):
        def __init__(self, model_name: str, base_url: str):
            self.model_name = model_name
            self.base_url = base_url

        def execute(self, prompt: str) -> str:
            # Use OpenAI-like API to connect to the model
            # Implement connection using Claude SDK
            return f"Response from {self.model_name} via Claude SDK"

    from models.openai_model import OpenAIModel
    from models.anthropic_model import AnthropicModel
    from models.generic_model import GenericModel

    # This can be dynamic based on configuration or other logic
    model = OpenAIModel()  # or AnthropicModel(), GenericModel("ModelName", "BaseURL")

    coding_agent = CodingAgent(
        name="Coding agent",
        instructions="Perform coding tasks efficiently.",
        model=model,
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
    result = coding_agent.model.execute("List the files in the current directory.")
    print(result)
