"""Keyboard-first connection management and an offline expiration inbox."""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from .config import AUTH_METHODS, FIELDS, Config, ConfigError
from .service import RefreshResult, Service
from .snowflake import safe_text
from .store import Store, expiry_label, stale


def literal(value: object, style: str = "") -> Text:
    return Text(safe_text(value), style=style)


class ConnectionForm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: Config, name: str | None = None):
        super().__init__()
        self.config = config
        self.name_to_edit = name
        self.values = config.profile(name, environment=False).settings if name else {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label(
                "Edit connection" if self.name_to_edit else "Add connection", classes="dialog-title"
            )
            yield Label("Name")
            yield Input(
                value=self.name_to_edit or "", id="field-name", disabled=bool(self.name_to_edit)
            )
            for field, label in (
                ("account", "Account identifier (organization-account)"),
                ("user", "Snowflake username"),
            ):
                yield Label(label)
                yield Input(value=self.values.get(field, ""), id=f"field-{field}")
            yield Label("Authentication")
            auth = self.values.get("authenticator", "externalbrowser")
            methods = list(dict.fromkeys([*AUTH_METHODS, auth]))
            yield Select(
                [(value, value) for value in methods],
                value=auth,
                allow_blank=False,
                id="field-authenticator",
            )
            for field, label in (
                ("role", "Role (optional)"),
                ("warehouse", "Warehouse (optional)"),
                ("token_file_path", "PAT file path (for token authentication)"),
                ("private_key_file", "Private key file path (for key-pair authentication)"),
                ("database", "Database (optional)"),
                ("schema", "Schema (optional)"),
                ("host", "Host override (optional; retain private connectivity settings)"),
            ):
                yield Label(label)
                yield Input(value=self.values.get(field, ""), id=f"field-{field}")
            yield Static(
                "Existing credentials and other settings are preserved. "
                "Enter file paths, not token values.",
                classes="muted",
                markup=False,
            )
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        name = self.query_one("#field-name", Input).value.strip()
        settings = {}
        for field in FIELDS:
            widget = self.query_one(f"#field-{field}")
            settings[field] = str(widget.value).strip()
        try:
            self.config.save(name, settings, create=self.name_to_edit is None)
        except (ConfigError, OSError) as exc:
            self.query_one("#form-error", Static).update(safe_text(exc))
        else:
            self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmRemove(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, name: str):
        super().__init__()
        self.connection_name = name

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm"):
            yield Static(
                f"Remove connection {self.connection_name!r}?", classes="dialog-title", markup=False
            )
            yield Static(
                "Removes the local profile. The Snowflake account, tokens, "
                "and cached expiry records remain.",
                markup=False,
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Remove connection", variant="error", id="confirm-remove")

    @on(Button.Pressed, "#confirm-remove")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class TokenBinding(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, store: Store, connection: dict):
        super().__init__()
        self.store = store
        self.connection = connection

    def compose(self) -> ComposeResult:
        choices = [("Not associated", "")]
        choices.extend(
            (safe_text(token["name"]), token["name"])
            for token in self.store.tokens()
            if token["account_id"] == self.connection["account_id"]
            and token["user_name"] == self.connection["user_name"]
        )
        current = self.connection["token_name"] or ""
        if current not in {value for _, value in choices}:
            current = ""
        with Vertical(classes="dialog confirm"):
            yield Label("Associate a PAT", classes="dialog-title")
            yield Static(
                "Record which token this connection uses. "
                "This does not change credentials or verify the association.",
                markup=False,
            )
            yield Select(choices, value=current, allow_blank=False, id="token-choice")
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Save association", variant="primary", id="save-binding")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#save-binding")
    def save(self) -> None:
        value = self.query_one("#token-choice", Select).value
        try:
            self.store.bind_token(self.connection["id"], str(value) or None)
        except ValueError as exc:
            self.query_one("#form-error", Static).update(str(exc))
        else:
            self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class Snowbeam(App):
    TITLE = "Snowbeam"
    SUB_TITLE = "Your way into Snowflake"
    CSS = """
    Screen { background: #0b1420; color: #dceaf5; }
    Header { background: #13263a; }
    Footer { background: #13263a; }
    #summary { height: 4; padding: 1 2; background: #101e2e; }
    #workspace { height: 1fr; }
    #inventory {
        width: 27; min-width: 19; padding: 1; background: #101e2e;
        border-right: solid #233d53;
    }
    #content { width: 1fr; }
    TabbedContent { height: 1fr; }
    ContentSwitcher { height: 1fr; }
    TabPane { padding: 0 1; }
    DataTable { height: 1fr; background: #0b1420; }
    DataTable > .datatable--header { background: #16334b; color: #a6dbf2; text-style: bold; }
    DataTable > .datatable--cursor { background: #21526d; color: #ffffff; }
    .pane-note { height: auto; max-height: 4; padding: 1 0; color: #9eb3c4; }
    #details { height: 6; padding: 1 2; background: #101e2e; border-top: solid #233d53; }
    #toolbar { height: 3; padding: 0 1; background: #101e2e; }
    #toolbar Button { min-width: 8; margin-right: 1; height: 3; border: none; }
    #message { height: auto; max-height: 4; padding: 0 2; color: #efc878; }
    ModalScreen { align: center middle; background: #060c14bb; }
    .dialog {
        width: 68; max-width: 96%; height: 90%; padding: 1 2;
        border: solid #62bad9; background: #13263a;
    }
    .confirm { height: auto; max-height: 90%; }
    .dialog-title { text-style: bold; color: #a6dbf2; margin-bottom: 1; height: auto; }
    .dialog Label { margin-top: 1; }
    .dialog Input, .dialog Select { margin-bottom: 0; }
    .dialog-actions { height: 3; margin-top: 1; }
    .dialog-actions Button { margin-right: 1; }
    #form-error { color: #ff9f94; height: auto; }
    .muted { color: #9eb3c4; height: auto; margin-top: 1; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_all", "Refresh all"),
        ("t", "refresh_selected", "Test selected"),
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("b", "bind", "Associate PAT"),
        ("o", "discover", "Discover accounts"),
        ("c", "copy_identifier", "Copy account"),
    ]

    def __init__(self, service: Service, *, auto_refresh: bool = True):
        super().__init__()
        self.service = service
        self.refresh_metadata = auto_refresh
        self.selected_connection: str | None = None
        self.scope: tuple[str, str] | None = None
        self.busy = False
        self.table_connections: dict[str, dict] = {}
        self.table_tokens: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="summary", markup=False)
        with Horizontal(id="workspace"):
            yield Tree("Known organizations", id="inventory")
            with Vertical(id="content"):
                with TabbedContent():
                    with TabPane("Connections", id="connections-tab"):
                        yield Static(
                            "Select a connection. T tests it and refreshes its token inventory.",
                            classes="pane-note",
                            id="connection-note",
                            markup=False,
                        )
                        yield DataTable(id="connections", cursor_type="row", zebra_stripes=True)
                    with TabPane("Tokens", id="tokens-tab"):
                        yield Static(
                            "PAT metadata only. Expiration is independent of connection health.",
                            classes="pane-note",
                            id="token-note",
                            markup=False,
                        )
                        yield DataTable(id="tokens", cursor_type="row", zebra_stripes=True)
                    with TabPane("Attention", id="attention-tab"):
                        yield Static("", classes="pane-note", id="attention-note", markup=False)
                        yield DataTable(id="attention", cursor_type="row", zebra_stripes=True)
        yield Static(
            "Select a connection or token to inspect its details.", id="details", markup=False
        )
        with Horizontal(id="toolbar"):
            yield Button("Refresh all", id="refresh", variant="primary")
            yield Button("Add", id="add")
            yield Button("Edit", id="edit")
            yield Button("Default", id="default")
            yield Button("Remove", id="remove")
        yield Static("", id="message", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#connections", DataTable).add_columns(
            "Connection", "Organization / account", "User", "Authentication", "Health", "PAT"
        )
        self.query_one("#tokens", DataTable).add_columns(
            "Token", "Account", "User", "Status", "Expires (UTC)", "Remaining"
        )
        self.query_one("#attention", DataTable).add_columns(
            "Account / user", "Token", "Attention", "Last verified"
        )
        self.reload()
        self.set_interval(60, self.render_data)
        if self.refresh_metadata and not self.service.demo:
            self.set_interval(3600, self.background_refresh)
            self.background_refresh()

    def message(self, value: str) -> None:
        self.query_one("#message", Static).update(safe_text(value))

    def reload(self, saved: bool = True) -> None:
        if not saved:
            return
        try:
            self.service.import_profiles()
        except (ConfigError, OSError) as exc:
            self.message(str(exc))
        self.render_data(rebuild_tree=True)

    def in_scope(self, row: dict) -> bool:
        if self.scope is None:
            return True
        kind, value = self.scope
        return (
            row.get("organization") == value
            if kind == "org"
            else row.get("account_id", row.get("id")) == value
        )

    def render_data(self, *, rebuild_tree: bool = False) -> None:
        store = self.service.store
        accounts = store.accounts()
        connections = [row for row in store.connections() if self.in_scope(row)]
        tokens = [row for row in store.tokens() if self.in_scope(row)]
        alerts = [row for row in store.alerts() if self.in_scope(row)]
        issues = self.service.coverage_issues()
        prefix = "DEMO · " if self.service.demo else ""
        self.query_one("#summary", Static).update(
            f"{prefix}{len(accounts)} accounts  ·  {len(store.connections())} connections  ·  "
            f"{len(store.tokens())} tokens  ·  {len(store.alerts())} need attention\n"
            + (
                "Verification incomplete — inspect Attention."
                if issues
                else "Configured connections and their PAT inventories checked within 24 hours."
            )
        )
        if rebuild_tree:
            tree = self.query_one("#inventory", Tree)
            tree.clear()
            tree.root.data = None
            organizations = {}
            for account in accounts:
                org = account["organization"]
                if org not in organizations:
                    organizations[org] = tree.root.add(
                        literal(org, "bold"), data=("org", org), expand=True
                    )
                organizations[org].add_leaf(
                    literal(account["name"]), data=("account", account["id"])
                )
            tree.root.expand()
        table = self.query_one("#connections", DataTable)
        table.clear()
        self.table_connections = {row["id"]: row for row in connections}
        for row in connections:
            account = (
                f"{row['organization']} / {row['account_name']}"
                if row["account_id"]
                else "Unverified"
            )
            health = "Verified" if row["status"] == "ok" else row["status"].replace("_", " ")
            if row["status"] == "ok" and stale(row["checked_at"]):
                health = "Stale check"
            table.add_row(
                literal(("★ " if row["is_default"] else "") + row["name"]),
                literal(account),
                literal(row["user_name"] or row["settings"].get("user")),
                literal(
                    {
                        "PROGRAMMATIC_ACCESS_TOKEN": "PAT",
                        "SNOWFLAKE_JWT": "Key pair",
                        "EXTERNALBROWSER": "Browser",
                    }.get(row["settings"].get("authenticator", "snowflake").upper(), "Other")
                ),
                literal(health, "green" if health == "Verified" else "yellow"),
                literal(row["token_name"] or "—"),
                key=row["id"],
            )
        if self.selected_connection in self.table_connections:
            table.move_cursor(row=list(self.table_connections).index(self.selected_connection))
        elif connections:
            self.selected_connection = connections[0]["id"]
        else:
            self.selected_connection = None
        self.query_one("#connection-note", Static).update(
            "Select a connection. T tests it and refreshes its token inventory."
            if connections
            else "No connections in this view. Press A to add one, or select All organizations."
        )
        token_table = self.query_one("#tokens", DataTable)
        token_table.clear()
        self.table_tokens = {}
        for index, row in enumerate(tokens):
            key = f"token-{index}"
            self.table_tokens[key] = row
            token_table.add_row(
                literal(row["name"]),
                literal(row["account_name"]),
                literal(row["user_name"]),
                literal(row["status"]),
                literal((row["expires_at"] or "Unknown")[:16].replace("T", " ")),
                literal(expiry_label(row["expires_at"])),
                key=key,
            )
        self.query_one("#token-note", Static).update(
            "PAT metadata only. Expiration is independent of connection health."
            if tokens
            else "No cached tokens in this view. Refresh a connection to inspect its user's PATs."
        )
        attention = self.query_one("#attention", DataTable)
        attention.clear()
        for row in alerts:
            label = (
                row["status"] if row["status"] not in {"ACTIVE", "EXPIRED"} else row["expiry_label"]
            )
            attention.add_row(
                literal(f"{row['organization']} / {row['account_name']} / {row['user_name']}"),
                literal(row["name"]),
                literal(label, "yellow"),
                literal(
                    row["checked_at"][:16].replace("T", " ") + (" · stale" if row["stale"] else "")
                ),
            )
        for issue in issues:
            attention.add_row(
                literal("Inventory"), literal("—"), literal(issue, "yellow"), literal("—")
            )
        self.query_one("#attention-note", Static).update(
            f"{len(alerts)} token alerts. {len(issues)} verification issues. "
            "Alerts include the next 14 days."
            if alerts or issues
            else "No upcoming expirations in the verified inventory."
        )
        self._buttons()
        self.show_active_details()

    def _buttons(self) -> None:
        for button_id in ("refresh", "add", "edit", "default", "remove"):
            self.query_one(f"#{button_id}", Button).disabled = self.busy or (
                button_id in {"edit", "default", "remove"} and not self.current()
            )

    @on(Tree.NodeSelected, "#inventory")
    def scope_selected(self, event: Tree.NodeSelected) -> None:
        self.scope = event.node.data
        self.render_data()

    @on(DataTable.RowHighlighted, "#connections")
    def connection_selected(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        if key in self.table_connections:
            self.selected_connection = key
            self.show_active_details()
            self._buttons()

    def show_connection_details(self, row: dict) -> None:
        self.query_one("#details", Static).update(
            safe_text(f"{row['name']}  ·  {row['message'] or row['status']}\n").rstrip()
            + f"\nConfiguration: {safe_text(row['source_path'])}"
            + f"\nLast verified: {safe_text(row['checked_at'] or 'Never')}"
            + "  ·  Role: "
            + safe_text(row["role_name"] or row["settings"].get("role", "Default"))
        )

    @on(DataTable.RowHighlighted, "#tokens")
    def token_selected(self, event: DataTable.RowHighlighted) -> None:
        if row := self.table_tokens.get(str(event.row_key.value)):
            if self.query_one(TabbedContent).active == "tokens-tab":
                self.show_token_details(row)

    def show_token_details(self, row: dict) -> None:
        self.query_one("#details", Static).update(
            f"{safe_text(row['organization'])} / {safe_text(row['account_name'])} / "
            f"{safe_text(row['user_name'])}"
            f"\n{safe_text(row['name'])}  ·  Role restriction: "
            f"{safe_text(row['role_restriction'] or 'None reported')}"
            f"\nExpires: {safe_text(row['expires_at'] or 'Unknown')}"
            f"  ·  Last verified: {safe_text(row['checked_at'])}"
        )

    @on(TabbedContent.TabActivated)
    def show_active_details(self) -> None:
        if not self.is_mounted:
            return
        active = self.query_one(TabbedContent).active
        if active == "connections-tab" and (row := self.current()):
            self.show_connection_details(row)
        elif active == "tokens-tab" and self.table_tokens:
            index = self.query_one("#tokens", DataTable).cursor_row
            self.show_token_details(
                list(self.table_tokens.values())[min(index, len(self.table_tokens) - 1)]
            )
        else:
            self.query_one("#details", Static).update(
                "Expiration dates are cached locally. "
                "Metadata older than 24 hours needs refreshing."
            )
        self._buttons()

    def current(self) -> dict | None:
        if self.query_one(TabbedContent).active != "connections-tab":
            return None
        return self.table_connections.get(self.selected_connection or "")

    def background_refresh(self) -> None:
        if not self.busy and not self.service.demo:
            try:
                if any(p.background_safe for p in self.service.config.profiles()):
                    self.request_refresh(interactive=False)
            except ConfigError as exc:
                self.message(str(exc))

    def request_refresh(
        self, name: str | None = None, *, interactive: bool = True, organization: bool = False
    ) -> None:
        if self.busy:
            return
        self.busy = True
        self._buttons()
        self.message(
            "Refreshing metadata… Browser authentication may require your attention."
            if interactive
            else "Refreshing connections that support unattended authentication…"
        )
        self.refresh_worker(name, interactive, organization)

    @work(thread=True)
    def refresh_worker(self, name: str | None, interactive: bool, organization: bool) -> None:
        try:
            result = self.service.refresh(name, interactive=interactive, organization=organization)
        except (ConfigError, OSError, ValueError) as exc:
            result = RefreshResult(issues=[safe_text(exc)])
        self.call_from_thread(self.finish_refresh, result)

    def finish_refresh(self, result: RefreshResult) -> None:
        self.busy = False
        self.render_data(rebuild_tree=True)
        self.message(f"Refreshed {result.refreshed} connection(s). " + " ".join(result.issues))

    @on(Button.Pressed, "#refresh")
    def action_refresh_all(self) -> None:
        self.request_refresh()

    def action_refresh_selected(self) -> None:
        if row := self.current():
            self.request_refresh(row["name"])

    def action_discover(self) -> None:
        if row := self.current():
            self.request_refresh(row["name"], organization=True)

    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        if not self.busy:
            self.push_screen(ConnectionForm(self.service.config), self.reload)

    @on(Button.Pressed, "#edit")
    def action_edit(self) -> None:
        if not self.busy and (row := self.current()):
            self.push_screen(ConnectionForm(self.service.config, row["name"]), self.reload)

    @on(Button.Pressed, "#default")
    def set_default(self) -> None:
        if row := self.current():
            try:
                self.service.config.set_default(row["name"])
                self.reload()
                self.message(f"Default connection: {row['name']}")
            except (ConfigError, OSError) as exc:
                self.message(str(exc))

    @on(Button.Pressed, "#remove")
    def remove(self) -> None:
        if row := self.current():

            def confirmed(remove: bool) -> None:
                if remove:
                    try:
                        self.service.config.remove(row["name"])
                        self.reload()
                        self.message(
                            "Local connection removed. Cached expiration records retained."
                        )
                    except (ConfigError, OSError) as exc:
                        self.message(str(exc))

            self.push_screen(ConfirmRemove(row["name"]), confirmed)

    def action_bind(self) -> None:
        if not self.busy and (row := self.current()):
            if not row["account_id"]:
                self.message("Refresh this connection before associating a token.")
            else:
                self.push_screen(TokenBinding(self.service.store, row), self.reload)

    def action_copy_identifier(self) -> None:
        if row := self.current():
            if row["organization"] and row["account_name"]:
                self.copy_to_clipboard(f"{row['organization']}-{row['account_name']}")
                self.message("Copied account identifier (requires terminal clipboard support).")
            else:
                self.message("Refresh this connection to verify its account identifier.")
