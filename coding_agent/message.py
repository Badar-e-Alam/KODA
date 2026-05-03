from dataclasses import dataclass
from typing import Literal

Role = Literal["user", "assistant", "tool", "system"]


@dataclass
class Message:
    role: Role
    content: str

    def __len__(self) -> int:
        return len(self.content)
