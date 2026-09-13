"""Edit organization and account annotations without contacting Snowflake."""

import sqlite3

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from .labels import ALIAS_LIMIT
from .snowflake import safe_text
from .store import Store


class LabelForm(ModalScreen[bool]):
    AUTO_FOCUS = "#label-alias"
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, store: Store, kind: str, key: str):
        super().__init__()
        self.store, self.kind, self.key = store, kind, key
        self.record = store.labels(kind, key)

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog", id="label-dialog"):
            yield Label(f"{self.kind.title()} alias and notes", classes="dialog-title")
            yield Static(safe_text(self.record["identifier"]), markup=False)
            yield Label("Alias (optional)")
            yield Input(value=self.record["alias"], max_length=ALIAS_LIMIT, id="label-alias")
            yield Label("Notes (optional)")
            yield TextArea(self.record["notes"], id="label-notes", tab_behavior="focus")
            yield Static(
                "Saved only on this device. Snowflake identifiers stay unchanged.\n"
                "Keep passwords, tokens, and private keys in your vault.",
                classes="muted",
                markup=False,
            )
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Save", variant="primary", id="label-save")
                yield Button("Cancel", id="label-cancel")

    @on(Button.Pressed, "#label-save")
    def save(self) -> None:
        try:
            self.store.set_labels(
                self.kind,
                self.key,
                alias=self.query_one("#label-alias", Input).value,
                notes=self.query_one("#label-notes", TextArea).text,
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "Could not save local labels."
            self.query_one("#form-error", Static).update(safe_text(message))
        else:
            self.dismiss(True)

    @on(Button.Pressed, "#label-cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)
