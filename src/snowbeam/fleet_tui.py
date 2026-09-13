"""Identity forms and complete, readable policy evidence for the terminal app."""

from __future__ import annotations

import json
import re

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from .config import ConfigError
from .snowflake import safe_text


class IdentityForm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, fleet, row: dict | None = None):
        super().__init__()
        self.fleet = fleet
        self.row = row

    def compose(self) -> ComposeResult:
        row = self.row or {}
        tracked = row.get("tracked", False)
        with VerticalScroll(classes="dialog"):
            yield Label(
                "Identity labels" if self.row else "Add an individual agent", classes="dialog-title"
            )
            if not self.row:
                templates = self.fleet.config.read()["templates"]
                yield Label("Account-bound provisioning template")
                yield Select([(key, key) for key in templates], id="identity-template")
                yield Static(
                    "Define reusable templates with snowbeam templates add. This form records "
                    "intent; P previews live provisioning.",
                    classes="muted",
                    markup=False,
                )
            yield Label("Snowbeam name")
            default_name = (
                row.get("id", "")
                if tracked
                else re.sub(r"[^A-Za-z0-9_-]", "-", row.get("connection", ""))
            )
            yield Input(value=default_name, id="identity-name", disabled=tracked)
            if not self.row:
                yield Label("Snowflake username (new user)")
                yield Input(placeholder="AGENT_SAM", id="identity-user")
                for field in (
                    "workload_provider",
                    "workload_subject",
                    "workload_issuer",
                    "workload_audience",
                    "workload_token_file",
                ):
                    yield Label(
                        field.replace("_", " ").capitalize() + " (WIF only)",
                        classes="workload-field",
                    )
                    yield Input(id="identity-" + field, classes="workload-field")
            for field in ("client", "owner", "purpose", "runtime"):
                yield Label(field.capitalize())
                yield Input(value=row.get(field, ""), id=f"identity-{field}")
            if self.row and not tracked:
                yield Label("Kind")
                yield Select(
                    [("Person", "person"), ("Agent", "agent")],
                    value="person",
                    allow_blank=False,
                    id="identity-kind",
                )
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Save", id="identity-save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        if not self.row:
            self.template_changed()

    @on(Select.Changed, "#identity-template")
    def template_changed(self):
        value = self.query_one("#identity-template", Select).value
        template = self.fleet.config.read()["templates"].get(str(value), {})
        for widget in self.query(".workload-field"):
            widget.display = template.get("auth") == "WIF"

    @on(Button.Pressed, "#identity-save")
    def save(self):
        key = self.query_one("#identity-name", Input).value.strip()
        labels = {
            field: self.query_one(f"#identity-{field}", Input).value.strip()
            for field in ("client", "owner", "purpose", "runtime")
        }
        try:
            if not self.row:
                template = self.query_one("#identity-template", Select).value
                if template is Select.BLANK or not labels["owner"] or not labels["runtime"]:
                    raise ValueError("Choose a template and supply the agent's owner and runtime.")
                user = self.query_one("#identity-user", Input).value.strip()
                labels.update(
                    {
                        f: self.query_one("#identity-" + f, Input).value.strip()
                        for f in (
                            "workload_provider",
                            "workload_subject",
                            "workload_issuer",
                            "workload_audience",
                            "workload_token_file",
                        )
                        if self.query_one("#identity-" + f, Input).value.strip()
                    }
                )
                self.fleet.config.add_agent(key, str(template), user, key, **labels)
            elif self.row["tracked"]:
                self.fleet.config.save("identities", key, labels)
            else:
                kind = str(self.query_one("#identity-kind", Select).value)
                self.fleet.config.track(key, self.row["connection"], kind=kind, **labels)
            self.dismiss(True)
        except (ValueError, OSError, ConfigError) as exc:
            self.query_one("#form-error", Static).update(safe_text(exc))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss(False)


class Evidence(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, row):
        super().__init__()
        self.row = row

    def compose(self):
        with VerticalScroll(classes="dialog evidence"):
            yield Label("Identity and security evidence", classes="dialog-title")
            yield Static(
                "Snowflake enforces access. Local intent and runtime labels are recorded "
                "by the operator. Missing or stale metadata is not proof of safety.",
                classes="muted",
                markup=False,
            )
            yield Static(json.dumps(self.row, indent=2, ensure_ascii=True), markup=False)
            yield Button("Close", id="close")

    @on(Button.Pressed, "#close")
    def action_close(self):
        self.dismiss()


class LifecycleChoice(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, row):
        super().__init__()
        self.row = row

    def compose(self):
        with VerticalScroll(classes="dialog confirm"):
            yield Label("Agent lifecycle", classes="dialog-title")
            yield Static(
                f"{safe_text(self.row['organization'])} / "
                f"{safe_text(self.row['account_name'])} / "
                f"{safe_text(self.row['user_name'])}",
                markup=False,
            )
            state = self.row["lifecycle"]
            choices = (
                [("Preview provisioning", "provision")]
                if not state
                else [
                    ("Verify on this runtime", "verify"),
                    ("Preview credential rotation", "rotate"),
                    ("Preview retirement of old credential", "retire-old"),
                    ("Preview disabling this agent", "revoke"),
                ]
            )
            yield Select(choices, value=choices[0][1], allow_blank=False, id="lifecycle-action")
            yield Static(
                f"Declared runtime: {safe_text(self.row['runtime'])}. Verify only when "
                f"running here. All changes require review of an account-bound plan.",
                classes="muted",
                markup=False,
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Continue", id="choose-action", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#choose-action")
    def choose(self):
        self.dismiss(str(self.query_one("#lifecycle-action", Select).value))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss(None)


class PlanReview(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, plan):
        super().__init__()
        self.plan = plan

    def compose(self):
        with VerticalScroll(classes="dialog evidence"):
            yield Label("Review the exact account and access plan", classes="dialog-title")
            yield Static(json.dumps(self.plan, indent=2, ensure_ascii=True), markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Apply this plan", id="apply-plan", variant="warning")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#apply-plan")
    def apply(self):
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss(False)
