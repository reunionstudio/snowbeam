"""Release checks and a confirmed install-and-restart action."""

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, LoadingIndicator, Static

from .config import ConfigError
from .installation import upgrade_instructions
from .snowflake import safe_text
from .updates import describe
from .upgrader import homebrew_upgrader


class UpdatePanel(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        manager, instructions = upgrade_instructions()
        self.can_install = homebrew_upgrader() is not None
        with VerticalScroll(classes="dialog"):
            yield Label("Snowbeam updates", classes="dialog-title")
            yield Static(describe(self.app.updates.status()), id="update-status", markup=False)
            if self.can_install:
                yield Static(
                    "Update and restart installs the new version through Homebrew, then reopens "
                    "Snowbeam with the same configuration. Homebrew may also update dependencies "
                    "and repair affected packages.",
                    classes="muted",
                )
            yield Static("", id="install-status", markup=False)
            yield LoadingIndicator(id="install-spinner")
            yield Button("Update and restart", id="install-update", variant="primary")
            yield Static(
                "Check public GitHub release metadata. No account or credential data is sent. "
                "Automatic checks are off by default and run at most daily while Snowbeam is open.",
                classes="muted",
            )
            yield Checkbox(
                "Check automatically once a day",
                self.app.service.preferences.automatic_updates(),
                id="automatic-updates",
                disabled=not self.app.network_allowed,
            )
            yield Static(
                f"Installation: {manager}\n\n{instructions}\n\n"
                "For a manual upgrade, quit Snowbeam first and reopen it afterward. "
                "Homebrew updates become available when the tap is updated. "
                "Your configuration and inventory stay in your user directory.",
                classes="muted",
                markup=False,
            )
            if not self.can_install:
                yield Static(
                    "The Update button requires an installation from the official Homebrew tap. "
                    "Use the instructions above for this installation.",
                    classes="muted",
                )
            if not self.app.network_allowed:
                yield Static("Online update checks are disabled in offline and demo mode.")
            with Horizontal(classes="dialog-actions"):
                yield Button("Check now", id="check-update", disabled=not self.app.network_allowed)
                yield Button("Close", id="close-updates")

    def on_mount(self) -> None:
        self.refresh_controls()

    def refresh_controls(self) -> None:
        installing = self.app.upgrading
        self.query_one("#install-spinner", LoadingIndicator).display = installing
        self.query_one("#install-update", Button).display = self.can_install
        self.query_one("#install-update", Button).disabled = (
            not self.can_install
            or not self.app.network_allowed
            or installing
            or self.app.checking_updates
            or not self.app.updates.status()["available"]
        )
        self.query_one("#check-update", Button).disabled = (
            not self.app.network_allowed or installing or self.app.checking_updates
        )
        self.query_one("#automatic-updates", Checkbox).disabled = (
            not self.app.network_allowed or installing
        )
        self.query_one("#close-updates", Button).disabled = installing

    def install_progress(self, message: str) -> None:
        self.query_one("#install-status", Static).update(safe_text(message))
        self.refresh_controls()

    @on(Button.Pressed, "#install-update")
    def install(self) -> None:
        self.app.install_update()

    def show_status(self) -> None:
        self.query_one("#update-status", Static).update(describe(self.app.updates.status()))
        self.refresh_controls()

    @on(Button.Pressed, "#check-update")
    def check(self) -> None:
        self.query_one("#check-update", Button).disabled = True
        self.query_one("#update-status", Static).update("Checking public releases…")
        self.app.check_updates()
        self.refresh_controls()

    @on(Checkbox.Changed, "#automatic-updates")
    def automatic(self, event: Checkbox.Changed) -> None:
        try:
            self.app.service.preferences.set_automatic_updates(event.value)
        except (ConfigError, OSError) as exc:
            self.query_one("#update-status", Static).update(safe_text(exc))
        else:
            self.app.background_update_check()

    @on(Button.Pressed, "#close-updates")
    def action_close(self) -> None:
        if not self.app.upgrading:
            self.dismiss()
