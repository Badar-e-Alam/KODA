"""
Session tree navigation widget for KODA.

Provides an interactive tree view of the session history,
allowing users to navigate between branches and select
any point to continue from.

Keybindings:
  Enter      — select the highlighted node
  Escape     — cancel navigation
  Up/Down    — move between nodes
  Left/Right — collapse/expand branches
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, Tree

from koda.session import SessionTree


class TreeScreen(ModalScreen[str | None]):
    """
    Modal screen showing the session tree.

    Returns the selected entry ID, or None if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    TreeScreen {
        align: center middle;
    }

    #tree-container {
        width: 90%;
        height: 85%;
        border: solid $success 40%;
        background: $surface;
        padding: 1 2;
    }

    #tree-title {
        height: 1;
        text-style: bold;
        color: $success;
        margin: 0 0 1 0;
    }

    #tree-help {
        height: 1;
        color: $text-muted;
        dock: bottom;
    }

    #session-tree {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, session: SessionTree) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="tree-container"):
            yield Static(
                "[bold green]Session Tree[/]  —  "
                "navigate and select a point to continue from (then choose memory mode)",
                id="tree-title",
            )
            tree: Tree[str] = Tree("Session", id="session-tree")
            tree.root.expand()
            self._build_tree(tree.root, None)
            yield tree
            yield Static(
                "[dim]Enter[/] select  |  "
                "[dim]Escape[/] cancel  |  "
                "[dim]Up/Down[/] navigate  |  "
                "[dim]Left/Right[/] collapse/expand",
                id="tree-help",
            )

    def _build_tree(self, node, parent_id: str | None) -> None:
        children = self._session.get_children(parent_id)
        children_sorted = sorted(
            children,
            key=lambda e: (
                not self._session.is_on_active_path(e.id),
                e.timestamp,
            ),
        )
        for entry in children_sorted:
            if entry.type == "header":
                self._build_tree(node, entry.id)
                continue

            is_active = self._session.is_on_active_path(entry.id)
            is_leaf = entry.id == self._session.leaf_id
            branch_count = self._session.get_branch_count(entry.id)

            icon = {
                "user": "[bold]>[/]",
                "assistant": "[green]<[/]",
            }.get(entry.role, "[dim]o[/]")

            if entry.type == "branch_summary":
                icon = "[yellow]~[/]"
            elif entry.type == "compaction":
                icon = "[cyan]=[/]"

            content = entry.content.replace("\n", " ").strip()
            if len(content) > 55:
                content = content[:52] + "..."

            markers = ""
            if is_leaf:
                markers += " [bold yellow]<- active[/]"
            elif is_active:
                markers += " [dim]*[/]"
            if branch_count > 1:
                markers += f" [cyan]({branch_count} branches)[/]"

            label = f"{icon} {content}{markers}"

            has_children = branch_count > 0
            if has_children:
                child_node = node.add(label, data=entry.id)
                if is_active:
                    child_node.expand()
            else:
                child_node = node.add_leaf(label, data=entry.id)

            self._build_tree(child_node, entry.id)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            self.dismiss(event.node.data)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CompressionChoiceScreen(ModalScreen[str | None]):
    """
    Ask whether to compress previous memory after selecting a tree node.

    Navigate with Up/Down arrows, select with Enter.

    Returns:
      - "compress" to summarize prior context
      - "keep" to keep full prior context
      - None when cancelled
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    CompressionChoiceScreen {
        align: center middle;
    }

    #compress-container {
        width: 78;
        height: auto;
        border: solid $success 40%;
        background: $surface;
        padding: 1 2;
    }

    #compress-title {
        text-style: bold;
        color: $success;
        margin: 0 0 1 0;
    }

    #compress-help {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    #compress-options {
        height: auto;
        max-height: 6;
        border: none;
        background: $surface;
        margin: 1 0 0 0;
    }

    #compress-options > .option-list--option-highlighted {
        background: $success 20%;
    }
    """

    def __init__(self, preview: str) -> None:
        super().__init__()
        self._preview = preview

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with Vertical(id="compress-container"):
            yield Static("Navigate With Memory Options", id="compress-title")
            yield Static(f"Target: [dim]{self._preview}[/]")
            yield OptionList(
                Option("[bold green]Compress[/]  —  summarize previous memory (uses /backend model)", id="compress"),
                Option("[bold cyan]Keep Full[/]  —  keep full previous memory", id="keep"),
                id="compress-options",
            )
            yield Static(
                "[dim]Up/Down[/] navigate  |  [dim]Enter[/] select  |  [dim]Esc[/] cancel",
                id="compress-help",
            )

    def on_mount(self) -> None:
        from textual.widgets import OptionList
        options = self.query_one("#compress-options", OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        selected_id = str(event.option.id)
        if selected_id in ("compress", "keep"):
            self.dismiss(selected_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
