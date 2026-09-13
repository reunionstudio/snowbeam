from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent, TextArea, Tree

from snowbeam.demo import demo_service
from snowbeam.labels_tui import LabelForm
from snowbeam.tui import ConfirmRemove, ConnectionForm, Snowbeam, TokenBinding


async def test_organization_and_account_label_forms_preserve_identifiers(tmp_path):
    service = demo_service(tmp_path)
    original_config = service.config.path.read_bytes()
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#labels", Button).disabled
        tree = app.query_one("#inventory", Tree)
        organization = next(n for n in tree.root.children if n.data == ("org", "ACME"))
        tree.select_node(organization)
        await pilot.pause()
        assert app.scope == ("org", "ACME")
        await pilot.click("#labels")
        assert isinstance(app.screen, LabelForm)
        app.screen.query_one("#label-alias", Input).value = "Acme [Accounting] LLC"
        app.screen.query_one("#label-notes", TextArea).load_text(
            "Finance team.\nReview access quarterly."
        )
        app.screen.query_one("#label-save", Button).press()
        await pilot.pause()
        assert app.scope == ("org", "ACME")
        assert "Acme [Accounting] LLC" in tree.root.children[0].label.plain
        assert tree.cursor_node.data == app.scope
        assert "ACME" in str(app.query_one("#scope-caption", Static).render())
        assert app.query_one("#connections", DataTable).row_count == 2
        app.action_labels()
        await pilot.pause()
        assert (
            app.screen.query_one("#label-notes", TextArea).text
            == "Finance team.\nReview access quarterly."
        )
        app.screen.query_one("#label-alias", Input).value = "Cancelled"
        await pilot.press("escape")
        assert service.store.labels("organization", "ACME")["alias"] == "Acme [Accounting] LLC"

        production = next(a for a in service.store.accounts() if a["name"] == "PRODUCTION")
        account_node = next(
            n for n in tree.root.children[0].children if n.data[1] == production["id"]
        )
        tree.select_node(account_node)
        await pilot.pause()
        tree.focus()
        await pilot.press("n")
        assert isinstance(app.screen, LabelForm)
        app.screen.query_one("#label-alias", Input).value = "Monthly close"
        app.screen.query_one("#label-notes", TextArea).load_text("No changes during close.")
        app.screen.query_one("#label-save", Button).press()
        await pilot.pause()
        assert "Monthly close" in app.account_label(app.current())
        assert "PRODUCTION" in str(app.query_one("#details", Static).render())
        app.action_copy_identifier()
        assert app.clipboard == "ACME-PRODUCTION"
        assert service.config.path.read_bytes() == original_config
        app.query_one(TabbedContent).active = "identities-tab"
        app.query_one("#identity-search", Input).value = "Monthly close"
        await pilot.pause()
        assert app.query_one("#identities", DataTable).row_count == 1


async def test_inventory_and_token_tabs_render_and_filter(tmp_path):
    service = demo_service(tmp_path)
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#connections", DataTable).row_count == 3
        assert app.query_one("#tokens", DataTable).row_count == 3
        assert app.query_one("#attention", DataTable).row_count == 2
        assert "Configuration:" in str(app.query_one("#details", Static).render())
        assert not app.query_one("#edit", Button).disabled
        account = next(a for a in service.store.accounts() if a["name"] == "PRODUCTION")
        app.scope = ("account", account["id"])
        app.render_data()
        await pilot.pause()
        assert app.query_one("#connections", DataTable).row_count == 1
        assert app.query_one("#tokens", DataTable).row_count == 1
        app.query_one(TabbedContent).active = "tokens-tab"
        await pilot.pause()
        assert "Expires:" in str(app.query_one("#details", Static).render())
        assert app.query_one("#edit", Button).disabled
        app.action_edit()
        assert not isinstance(app.screen, ConnectionForm)
        app.query_one(TabbedContent).active = "connections-tab"
        await pilot.pause()
        assert "Configuration:" in str(app.query_one("#details", Static).render())
        assert not app.query_one("#edit", Button).disabled
        app.save_screenshot("snowbeam-demo.svg", path=str(tmp_path))


async def test_add_edit_and_remove_through_actual_forms(tmp_path):
    service = demo_service(tmp_path)
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(100, 42)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ConnectionForm)
        app.screen.query_one("#field-name", Input).value = "new-profile"
        app.screen.query_one("#field-account", Input).value = "ACME-TEST"
        app.screen.query_one("#field-user", Input).value = "BOB"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert service.config.profile("new-profile").settings["user"] == "BOB"
        app.selected_connection = service.config.profile("new-profile").key
        app.action_edit()
        await pilot.pause()
        app.screen.query_one("#field-role", Input).value = "REVIEWER"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert service.config.profile("new-profile").settings["role"] == "REVIEWER"
        app.remove()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRemove)
        app.screen.query_one("#confirm-remove", Button).press()
        await pilot.pause()
        assert "new-profile" not in [p.name for p in service.config.profiles()]


async def test_add_invites_cloning_and_source_changes_keep_a_custom_name(tmp_path, monkeypatch):
    service = demo_service(tmp_path)
    service.config.save(
        "development", {"token_file_path": "/tokens/source", "warehouse": "COMPUTE"}
    )
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_DEVELOPMENT_WAREHOUSE", "ENVIRONMENT_OVERRIDE")
    app = Snowbeam(service, auto_refresh=False)
    before = service.config.source.read_bytes()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        form = app.screen
        source = form.query_one("#clone-from", Select)
        assert source.value is Select.BLANK
        assert source.region.y < form.query_one("#field-name", Input).region.y
        assert form.query_one("#field-account", Input).value == ""
        source.value = "development"
        await pilot.pause()
        assert form.query_one("#field-name", Input).value == "development-copy"
        assert form.query_one("#field-account", Input).value == "ACME-DEVELOPMENT"
        assert form.query_one("#field-warehouse", Input).value == "COMPUTE"
        assert form.query_one("#field-token_file_path", Input).value == ""
        form.query_one("#field-name", Input).value = "my-reporting"
        source.value = "northwind"
        await pilot.pause()
        assert form.query_one("#field-name", Input).value == "my-reporting"
        assert form.query_one("#field-account", Input).value == "NORTHWIND-ANALYTICS"
        assert form.query_one("#field-authenticator", Select).value == "externalbrowser"
        source.value = Select.BLANK
        await pilot.pause()
        assert form.query_one("#field-name", Input).value == "my-reporting"
        assert form.query_one("#field-account", Input).value == ""
        assert form.query_one("#field-warehouse", Input).value == ""
        await pilot.press("escape")
        assert service.config.source.read_bytes() == before


async def test_clone_action_prefills_and_creates_a_new_connection(tmp_path):
    service = demo_service(tmp_path)
    service.config.save("development-copy", {"account": "ACME-DEV", "user": "OLD"}, create=True)
    source_settings = service.config.profile("development", environment=False).settings
    original_fleet = service.fleet.config.path.read_bytes()
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(80, 24)) as pilot:
        app.selected_connection = service.config.profile("development").key
        app.render_data()
        await pilot.pause()
        assert not app.query_one("#clone", Button).disabled
        assert app.query_one("#updates", Button).region.right <= 80
        await pilot.press("shift+c")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ConnectionForm)
        assert form.query_one("#clone-from", Select).value == "development"
        assert form.query_one("#field-name", Input).value == "development-copy-2"
        assert form.query_one("#field-user", Input).value == "JANE"
        form.query_one("#field-user", Input).value = "BOB"
        form.query_one("#field-role", Input).value = "READER"
        form.query_one("#save", Button).focus()
        await pilot.pause()
        assert form.query_one("#save", Button).region.bottom <= 24
        await pilot.press("enter")
        await pilot.pause()
        cloned = service.config.profile("development-copy-2", environment=False)
        assert cloned.settings == source_settings | {"user": "BOB", "role": "READER"}
        assert not cloned.is_default
        assert service.config.profile("development", environment=False).settings == source_settings
        assert service.fleet.config.path.read_bytes() == original_fleet
        row = next(c for c in service.store.connections() if c["name"] == cloned.name)
        assert row["status"] == "not_checked" and row["token_name"] is None
        app.query_one(TabbedContent).active = "tokens-tab"
        await pilot.pause()
        assert app.query_one("#clone", Button).disabled
        app.action_clone()
        assert not isinstance(app.screen, ConnectionForm)


async def test_removed_clone_source_reports_error_without_losing_the_form(service):
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test() as pilot:
        app.action_add()
        await pilot.pause()
        service.config.remove("work")
        form = app.screen
        form.query_one("#field-name", Input).value = "my-new-connection"
        form.query_one("#clone-from", Select).value = "work"
        await pilot.pause()
        assert "does not exist" in str(form.query_one("#form-error", Static).render())
        assert form.query_one("#clone-from", Select).value is Select.BLANK
        assert form.query_one("#field-name", Input).value == "my-new-connection"
        await pilot.press("escape")


async def test_clone_does_not_confuse_same_names_in_different_configs(service, tmp_path):
    from snowbeam.config import Config

    other = Config(tmp_path / "other/config.toml")
    other.save("work", {"account": "OTHER-ACCOUNT", "user": "OTHER"}, create=True)
    service.store.sync_profiles(other.profiles(), other.path)
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test() as pilot:
        app.selected_connection = other.profile("work").key
        app.render_data()
        await pilot.pause()
        assert app.query_one("#clone", Button).disabled
        app.action_clone()
        await pilot.pause()
        assert not isinstance(app.screen, ConnectionForm)
        assert "--snow-config" in str(app.query_one("#message", Static).render())


async def test_selected_refresh_is_nonblocking_and_binding_is_explicit(tmp_path):
    service = demo_service(tmp_path)
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        app.selected_connection = service.config.profile("development").key
        app.action_refresh_selected()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.busy
        app.action_bind()
        await pilot.pause()
        assert isinstance(app.screen, TokenBinding)
        app.screen.query_one("#token-choice", Select).value = "DEVELOPMENT"
        app.screen.query_one("#save-binding", Button).press()
        await pilot.pause()
        row = next(r for r in service.store.connections() if r["name"] == "development")
        assert row["token_name"] == "DEVELOPMENT"


async def test_empty_app_is_usable_at_small_terminal_size(service):
    service.config.remove("work")
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one("#connections", DataTable).row_count == 0
        assert app.query_one("#add", Button).region.bottom <= 24
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ConnectionForm)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConnectionForm)


async def test_alias_editor_scrolls_and_saves_at_small_terminal_size(tmp_path):
    app = Snowbeam(demo_service(tmp_path), auto_refresh=False)
    async with app.run_test(size=(80, 24)) as pilot:
        tree = app.query_one("#inventory", Tree)
        tree.select_node(tree.root.children[0])
        await pilot.pause()
        assert app.query_one("#labels", Button).region.bottom <= 14
        await pilot.click("#labels")
        app.screen.query_one("#label-alias", Input).value = "Small terminal"
        await pilot.press("tab")
        assert isinstance(app.focused, TextArea)
        await pilot.press("tab")
        assert app.focused.id == "label-save"
        assert app.focused.region.bottom <= 24
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, LabelForm)
        assert app.service.store.labels("organization", "ACME")["alias"] == "Small terminal"


async def test_identity_search_labels_add_form_and_evidence_are_independent(tmp_path):
    from snowbeam.fleet_tui import Evidence, IdentityForm

    service = demo_service(tmp_path)
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one(TabbedContent).active = "identities-tab"
        await pilot.pause()
        assert app.query_one("#identities", DataTable).row_count == 4
        app.query_one("#identity-search", Input).value = "Northwind"
        await pilot.pause()
        assert app.query_one("#identities", DataTable).row_count == 1
        assert app.current_identity()["id"] == "jane-northwind"
        app.action_edit()
        await pilot.pause()
        assert isinstance(app.screen, IdentityForm)
        app.screen.query_one("#identity-owner", Input).value = "Jane and Bob"
        app.screen.query_one("#identity-save", Button).press()
        await pilot.pause()
        assert service.fleet.config.spec("jane-northwind")["owner"] == "Jane and Bob"
        assert service.config.profile("northwind").settings["user"] == "JANE_CONSULTANT"
        app.action_evidence()
        await pilot.pause()
        assert isinstance(app.screen, Evidence)
        await pilot.press("escape")
        app.action_add()
        await pilot.pause()
        assert isinstance(app.screen, IdentityForm)
        app.screen.query_one("#identity-template", Select).value = "acme-reporting"
        app.screen.query_one("#identity-name", Input).value = "new-agent"
        app.screen.query_one("#identity-user", Input).value = "NEW_AGENT"
        app.screen.query_one("#identity-owner", Input).value = "Jane"
        app.screen.query_one("#identity-runtime", Input).value = "runner-west"
        app.screen.query_one("#identity-save", Button).press()
        await pilot.pause()
        assert service.fleet.config.spec("new-agent")["user"] == "NEW_AGENT"
        assert "new-agent" not in [p.name for p in service.config.profiles()]
        assert not service.store.operations()
