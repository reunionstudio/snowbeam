"""Keyboard-first connection management and an offline expiration inbox."""

from __future__ import annotations

import re

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
from .fleet_tui import Evidence, IdentityForm, LifecycleChoice, PlanReview
from .labels import display_name
from .labels_tui import LabelForm
from .provision import Provisioner
from .service import RefreshResult, Service
from .snowflake import SnowError, safe_text
from .store import Store, expiry_label, stale
from .updates import Updates
from .updates_tui import UpdatePanel
from .upgrader import HomebrewUpgrade, UpgradeError, homebrew_upgrader


def literal(value: object, style: str = "") -> Text:
    return Text(safe_text(value), style=style)


class ConnectionForm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: Config, name: str | None = None, *, clone_from: str | None = None):
        super().__init__()
        self.config = config
        self.name_to_edit = name
        self.clone_from = clone_from
        self.sources = [] if name else config.profiles(environment=False)
        self.suggested_name = self.clone_name(clone_from) if clone_from else ""
        self.values = (
            config.profile(name, environment=False).settings
            if name
            else config.clone_settings(clone_from)
            if clone_from
            else {}
        )
        self.AUTO_FOCUS = "#field-account" if name else "#clone-from"
        if not name and not self.sources:
            self.AUTO_FOCUS = "#field-name"

    def clone_name(self, source: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", source).strip("-_.") or "connection"
        names = {profile.name for profile in self.sources}
        number = 1
        while True:
            suffix = "-copy" if number == 1 else f"-copy-{number}"
            name = base[: 128 - len(suffix)] + suffix
            if name not in names:
                return name
            number += 1

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(
                "Edit connection" if self.name_to_edit else "Add connection",
                classes="dialog-title",
            )
            if not self.name_to_edit:
                yield Label("Clone from (optional)")
                yield Select(
                    [(literal(profile.name), profile.name) for profile in self.sources],
                    value=self.clone_from or Select.BLANK,
                    prompt="Start with a blank connection",
                    id="clone-from",
                    disabled=not self.sources,
                )
                yield Static(
                    "Choose an existing connection, then edit the details below."
                    if self.sources
                    else "No saved connections yet. Enter your first connection below.",
                    classes="muted clone-invitation",
                    markup=False,
                )
            with VerticalScroll(id="connection-fields"):
                yield Label("Name")
                yield Input(
                    value=self.name_to_edit or self.suggested_name,
                    id="field-name",
                    disabled=bool(self.name_to_edit),
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
                    (
                        "workload_identity_provider",
                        "Workload identity provider (AWS, AZURE, GCP, OIDC)",
                    ),
                ):
                    yield Label(label)
                    yield Input(value=self.values.get(field, ""), id=f"field-{field}")
                yield Static(
                    "Existing credentials and other settings are preserved. "
                    "Enter file paths, not token values."
                    if self.name_to_edit
                    else "Cloning copies connection settings. Credentials and vault bindings "
                    "must be configured separately.",
                    classes="muted",
                    markup=False,
                )
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Select.Changed, "#clone-from")
    def choose_source(self, event: Select.Changed) -> None:
        source = str(event.value) if event.value is not Select.BLANK else None
        if source == self.clone_from:
            return
        try:
            values = self.config.clone_settings(source) if source else {}
        except (ConfigError, OSError) as exc:
            self.query_one("#form-error", Static).update(safe_text(exc))
            event.select.value = self.clone_from or Select.BLANK
            return
        name = self.query_one("#field-name", Input)
        suggested = self.clone_name(source) if source else ""
        if not name.value or name.value == self.suggested_name:
            name.value = suggested
        self.clone_from, self.suggested_name, self.values = source, suggested, values
        auth = values.get("authenticator", "externalbrowser")
        authentication = self.query_one("#field-authenticator", Select)
        authentication.set_options(
            [(method, method) for method in dict.fromkeys([*AUTH_METHODS, auth])]
        )
        authentication.value = auth
        for field in FIELDS:
            if field != "authenticator":
                self.query_one(f"#field-{field}", Input).value = values.get(field, "")
        self.query_one("#form-error", Static).update("")

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
    #sidebar {
        width: 32; min-width: 19; background: #101e2e;
        border-right: solid #233d53;
    }
    #inventory { height: 1fr; padding: 1; background: #101e2e; }
    #scope-caption { height: auto; max-height: 4; padding: 0 1; color: #9eb3c4; }
    #labels { margin: 0 1; height: 3; width: 1fr; }
    #label-notes { height: 8; min-height: 4; }
    #label-dialog { height: auto; max-height: 90%; }
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
    .evidence { width: 96; }
    #connection-fields { height: 1fr; }
    .muted.clone-invitation { margin-top: 0; }
    #identity-search { height: 3; margin-bottom: 1; }
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
        ("shift+c", "clone", "Clone"),
        ("e", "edit", "Edit"),
        ("n", "labels", "Alias / notes"),
        ("b", "bind", "Associate PAT"),
        ("o", "discover", "Discover accounts"),
        ("c", "copy_identifier", "Copy account"),
        ("p", "lifecycle", "Agent lifecycle"),
        ("u", "updates", "Updates"),
        ("enter", "evidence", "Details"),
    ]

    def __init__(self, service: Service, *, auto_refresh: bool = True):
        super().__init__()
        self.service = service
        self.refresh_metadata = auto_refresh
        self.network_allowed = auto_refresh and not service.demo
        self.updates = Updates(service.store.directory)
        self.checking_updates = False
        self.upgrading = False
        self.selected_connection: str | None = None
        self.scope: tuple[str, str] | None = None
        self.accounts: dict[str, dict] = {}
        self.busy = False
        self.table_connections: dict[str, dict] = {}
        self.table_tokens: dict[str, dict] = {}
        self.table_identities: dict[str, dict] = {}
        self.selected_identity: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="summary", markup=False)
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Tree("Organizations", id="inventory")
                yield Static("", id="scope-caption", markup=False)
                yield Button("Alias / notes", id="labels", disabled=True)
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
                    with TabPane("Identities", id="identities-tab"):
                        yield Input(
                            placeholder="Search client, identity, account, owner, or runtime",
                            id="identity-search",
                        )
                        yield Static(
                            "Enter: full security evidence · E: labels · A: agent · T: inspect · "
                            "P: lifecycle",
                            classes="pane-note",
                            markup=False,
                        )
                        yield DataTable(id="identities", cursor_type="row", zebra_stripes=True)
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
            yield Button("Clone", id="clone")
            yield Button("Edit", id="edit")
            yield Button("Default", id="default")
            yield Button("Remove", id="remove")
            yield Button("Updates", id="updates")
        yield Static("", id="message", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#connections", DataTable).add_columns(
            "Connection", "Organization / account", "User", "Authentication", "Health", "PAT"
        )
        self.query_one("#identities", DataTable).add_columns(
            "Identity", "Security", "Client", "Account", "Runtime"
        )
        self.query_one("#tokens", DataTable).add_columns(
            "Token", "Account", "User", "Status", "Expires (UTC)", "Remaining"
        )
        self.query_one("#attention", DataTable).add_columns(
            "Account / user", "Token", "Attention", "Last verified"
        )
        self.reload()
        self.set_interval(60, self.render_data)
        self.update_button()
        if self.refresh_metadata and not self.service.demo:
            self.set_interval(60, self.background_refresh)
            self.background_refresh()
            self.set_interval(3600, self.background_update_check)
            self.background_update_check()

    def update_button(self) -> None:
        self.screen_stack[0].query_one("#updates", Button).label = (
            "Update available" if self.updates.status()["available"] else "Updates"
        )

    @on(Button.Pressed, "#updates")
    def action_updates(self) -> None:
        if len(self.screen_stack) > 1:
            return
        try:
            self.service.preferences.automatic_updates()
        except (ConfigError, OSError) as exc:
            self.message(str(exc))
        else:
            self.push_screen(UpdatePanel())

    def background_update_check(self) -> None:
        if not self.network_allowed or self.checking_updates:
            return
        try:
            if self.service.preferences.automatic_updates() and self.updates.due():
                self.check_updates()
        except (ConfigError, OSError) as exc:
            self.message(str(exc))

    def check_updates(self) -> None:
        if not self.network_allowed or self.checking_updates or self.upgrading:
            return
        self.checking_updates = True
        self.update_worker()

    @work(thread=True)
    def update_worker(self) -> None:
        try:
            self.updates.check()
        except OSError:
            self.call_from_thread(self.message, "Could not save the update-check result.")
        finally:
            self.call_from_thread(self.finish_update_check)

    def finish_update_check(self) -> None:
        self.checking_updates = False
        self.update_button()
        if isinstance(self.screen, UpdatePanel):
            self.screen.show_status()

    def install_update(self) -> None:
        if not self.network_allowed or self.upgrading:
            return
        if self.busy or self.checking_updates:
            self.install_failed("Wait for the current operation to finish, then update Snowbeam.")
            return
        updater = homebrew_upgrader()
        status = self.updates.status()
        if updater is None or not status["available"]:
            self.install_failed("A verified Homebrew installation and newer release are required.")
            return
        # This is the confirmation boundary: only the user's button invokes it.
        self.upgrading = self.busy = True
        self.install_progress("Preparing the update…")
        self.install_worker(updater, status["latest"])

    @work(thread=True)
    def install_worker(self, updater: HomebrewUpgrade, version: str) -> None:
        try:
            restart = updater.install(
                version, lambda message: self.call_from_thread(self.install_progress, message)
            )
        except UpgradeError as exc:
            self.call_from_thread(self.install_failed, str(exc))
        else:
            self.call_from_thread(self.exit, restart)

    def install_progress(self, message: str) -> None:
        if isinstance(self.screen, UpdatePanel):
            self.screen.install_progress(message)

    def install_failed(self, message: str) -> None:
        # An active Snowflake worker owns busy unless this was an installation.
        if self.upgrading:
            self.upgrading = self.busy = False
        self.install_progress(message)

    def action_quit(self) -> None:
        if self.upgrading:
            self.install_progress("Update in progress. Snowbeam will restart when it finishes.")
        else:
            self.exit()

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
        rebuild_tree = rebuild_tree or accounts != list(self.accounts.values())
        self.accounts = {row["id"]: row for row in accounts}
        if self.scope and not any(self.in_scope(row) for row in accounts):
            self.scope = None
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
            tree.move_cursor(None)
            tree.clear()
            tree.root.data = None
            selected = tree.root
            nodes = [tree.root]
            organizations = {}
            for account in accounts:
                org = account["organization"]
                if org not in organizations:
                    organizations[org] = tree.root.add(
                        literal(account["organization_alias"] or org, "bold"),
                        data=("org", org),
                        expand=True,
                    )
                    nodes.append(organizations[org])
                node = organizations[org].add_leaf(
                    literal(account["account_alias"] or account["name"]),
                    data=("account", account["id"]),
                )
                nodes.append(node)
                if node.data == self.scope:
                    selected = node
                elif organizations[org].data == self.scope:
                    selected = organizations[org]
            tree.root.expand()
            tree.move_cursor_to_line(nodes.index(selected))
        self.show_scope()
        table = self.query_one("#connections", DataTable)
        table.clear()
        self.table_connections = {row["id"]: row for row in connections}
        for row in connections:
            account = self.account_label(row) if row["account_id"] else "Unverified"
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
                        "WORKLOAD_IDENTITY": "Workload",
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
            else "No connections in this view. Press A to add one, or select Organizations."
        )
        token_table = self.query_one("#tokens", DataTable)
        token_table.clear()
        self.table_tokens = {}
        for index, row in enumerate(tokens):
            key = f"token-{index}"
            self.table_tokens[key] = row
            token_table.add_row(
                literal(row["name"]),
                literal(self.account_label(row, organization=False)),
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
                literal(f"{self.account_label(row)} / {row['user_name']}"),
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
        self.render_identities()
        self._buttons()
        self.show_active_details()

    def _buttons(self) -> None:
        for button_id in ("refresh", "add", "clone", "edit", "default", "remove"):
            self.query_one(f"#{button_id}", Button).disabled = self.busy or (
                (button_id == "edit" and not (self.current() or self.current_identity()))
                or (button_id in {"clone", "default", "remove"} and not self.current())
                or (
                    button_id == "clone"
                    and self.current()
                    and self.current()["config_path"] != str(self.service.config.path)
                )
            )

    @on(Tree.NodeSelected, "#inventory")
    def scope_selected(self, event: Tree.NodeSelected) -> None:
        self.scope = event.node.data
        self.render_data()

    def account_label(self, row: dict, *, organization: bool = True, identifiers: bool = False):
        account = self.accounts.get(row.get("account_id", row.get("id")), {})
        org = row.get("organization") or "?"
        name = row.get("account_name") or account.get("name") or "?"
        org_alias, alias = account.get("organization_alias"), account.get("account_alias")
        if identifiers:
            org, name = display_name(org, org_alias), display_name(name, alias)
        else:
            org, name = org_alias or org, alias or name
        return f"{org} / {name}" if organization else name

    def show_scope(self) -> None:
        self.query_one("#labels", Button).disabled = self.scope is None
        caption = "Select an organization or account to add an alias and notes."
        if self.scope:
            kind, key = self.scope
            record = self.service.store.labels("organization" if kind == "org" else kind, key)
            caption = safe_text(display_name(record["identifier"], record["alias"]))
            if record["notes"]:
                caption += "\n" + "\n".join(
                    safe_text(line) for line in record["notes"].splitlines()[:3]
                )
        self.query_one("#scope-caption", Static).update(caption)

    @on(Button.Pressed, "#labels")
    def action_labels(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.scope:
            self.message("Select an organization or account in the tree, then press N.")
            return
        kind, key = self.scope

        def saved(changed: bool) -> None:
            if changed:
                self.render_data(rebuild_tree=True)
                self.message("Alias and notes saved on this device.")

        self.push_screen(
            LabelForm(self.service.store, "organization" if kind == "org" else kind, key), saved
        )

    @on(DataTable.RowHighlighted, "#connections")
    def connection_selected(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        if key in self.table_connections:
            self.selected_connection = key
            self.show_active_details()
            self._buttons()

    def show_connection_details(self, row: dict) -> None:
        self.query_one("#details", Static).update(
            safe_text(
                f"{row['organization'] or '?'}-{row['account_name'] or '?'}  ·  "
                f"{row['name']}  ·  {row['message'] or row['status']}"
            )
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
        elif active == "identities-tab" and (row := self.current_identity()):
            self.query_one("#details", Static).update(
                safe_text(
                    f"{row['id']} · Snowflake user: {row['user_name']} · Owner: {row['owner']}"
                )
                + "\n"
                + safe_text(
                    "; ".join(row["issues"][:3])
                    or "Scoped security metadata inspected; see evidence for coverage."
                )
                + "\nEnter opens policies, network rules, grants, credentials, and "
                "verification times."
            )
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
                if self.service.refresh_due():
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
            result = self.service.refresh(
                name, interactive=interactive, organization=organization, scheduled=not interactive
            )
        except (ConfigError, OSError, ValueError) as exc:
            result = RefreshResult(issues=[safe_text(exc)])
        self.call_from_thread(self.finish_refresh, result)

    def finish_refresh(self, result: RefreshResult) -> None:
        self.busy = False
        self.render_data(rebuild_tree=True)
        self.message(f"Refreshed {result.refreshed} connection(s). " + " ".join(result.issues))

    @on(Button.Pressed, "#refresh")
    def action_refresh_all(self) -> None:
        if self.query_one(TabbedContent).active == "identities-tab":
            self.request_fleet("refresh")
        else:
            self.request_refresh()

    def action_refresh_selected(self) -> None:
        if row := self.current_identity():
            self.request_fleet("refresh", row["id"])
        elif row := self.current():
            self.request_refresh(row["name"])

    def action_discover(self) -> None:
        if row := self.current():
            self.request_refresh(row["name"], organization=True)

    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        if not self.busy:
            if self.query_one(TabbedContent).active == "identities-tab":
                self.push_screen(IdentityForm(self.service.fleet), self.reload)
            else:
                try:
                    self.push_screen(ConnectionForm(self.service.config), self.reload)
                except (ConfigError, OSError) as exc:
                    self.message(str(exc))

    @on(Button.Pressed, "#clone")
    def action_clone(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.busy and (row := self.current()):
            if row["config_path"] != str(self.service.config.path):
                self.message(
                    "Open this connection's configuration with --snow-config before cloning it."
                )
                return
            try:
                self.push_screen(
                    ConnectionForm(self.service.config, clone_from=row["name"]), self.reload
                )
            except (ConfigError, OSError) as exc:
                self.message(str(exc))

    @on(Button.Pressed, "#edit")
    def action_edit(self) -> None:
        if self.busy:
            return
        if row := self.current_identity():
            self.push_screen(IdentityForm(self.service.fleet, row), self.reload)
        elif row := self.current():
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

    def render_identities(self) -> None:
        search = self.query_one("#identity-search", Input).value.casefold()
        try:
            rows = [r for r in self.service.fleet.identities() if self.in_scope(r)]
        except (ConfigError, ValueError, OSError) as exc:
            self.message(str(exc))
            rows = []
        rows = [
            r
            for r in rows
            if search
            in " ".join(
                str(r.get(f) or "")
                for f in (
                    "id",
                    "client",
                    "organization",
                    "account_name",
                    "user_name",
                    "owner",
                    "runtime",
                )
            ).casefold()
            + " "
            + self.account_label(r, identifiers=True).casefold()
        ]
        table = self.query_one("#identities", DataTable)
        table.clear()
        self.table_identities = {r["id"]: r for r in rows}
        for row in rows:
            table.add_row(
                literal(row["id"]),
                literal(
                    row["status"],
                    "cyan" if row["status"] in {"Matches template", "Inspected"} else "yellow",
                ),
                literal(row["client"]),
                literal(self.account_label(row)),
                literal(row["runtime"]),
                key=row["id"],
            )
        if self.selected_identity in self.table_identities:
            table.move_cursor(row=list(self.table_identities).index(self.selected_identity))
        elif rows:
            self.selected_identity = rows[0]["id"]
        else:
            self.selected_identity = None

    @on(Input.Changed, "#identity-search")
    def search_identities(self) -> None:
        if self.is_mounted:
            self.render_identities()
            self.show_active_details()

    @on(DataTable.RowHighlighted, "#identities")
    def identity_selected(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        if key in self.table_identities:
            self.selected_identity = key
            self.show_active_details()

    def current_identity(self) -> dict | None:
        if self.query_one(TabbedContent).active != "identities-tab":
            return None
        return self.table_identities.get(self.selected_identity or "")

    def action_evidence(self) -> None:
        if row := self.current_identity():
            self.push_screen(Evidence(row))

    def action_lifecycle(self) -> None:
        row = self.current_identity()
        if self.busy or not row:
            return
        if not row["template"]:
            self.message("Lifecycle actions apply to agents created from a provisioning template.")
            return

        def selected(action):
            if action:
                self.request_fleet("verify" if action == "verify" else "plan", row["id"], action)

        self.push_screen(LifecycleChoice(row), selected)

    def request_fleet(
        self,
        command: str,
        identity: str | None = None,
        action: str = "provision",
        approval: str = "",
    ) -> None:
        if self.busy:
            return
        self.busy = True
        self._buttons()
        self.message(
            "Reading security metadata…"
            if command == "refresh"
            else "Preparing the identity operation…"
        )
        self.fleet_worker(command, identity, action, approval)

    @work(thread=True)
    def fleet_worker(self, command, identity, action, approval) -> None:
        try:
            fleet = self.service.fleet
            if command == "refresh":
                result = fleet.refresh(identity)
            elif command == "plan":
                result = Provisioner(fleet).plan(identity, action)
            elif command == "apply":
                result = Provisioner(fleet).apply(identity, action, approval)
            else:
                result = Provisioner(fleet).verify(identity, fleet.config.spec(identity)["runtime"])
        except (ValueError, ConfigError, SnowError, OSError) as exc:
            result = {"error": safe_text(exc)}
        self.call_from_thread(self.finish_fleet, command, result)

    def finish_fleet(self, command, result) -> None:
        self.busy = False
        self.reload()
        if "error" in result:
            self.message(result["error"])
        elif command == "plan":

            def reviewed(approved):
                if approved:
                    self.request_fleet(
                        "apply", result["identity"], result["action"], result["approval"]
                    )

            self.push_screen(PlanReview(result), reviewed)
        else:
            self.message(
                "; ".join(result.get("issues", []))
                or result.get("next")
                or "Identity operation completed."
            )
