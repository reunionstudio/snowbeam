from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent

from snowbeam.demo import demo_service
from snowbeam.tui import ConfirmRemove, ConnectionForm, Snowbeam, TokenBinding


async def test_inventory_and_token_tabs_render_and_filter(tmp_path):
    service = demo_service(tmp_path)
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#connections", DataTable).row_count == 2
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
