"""Regenerate README screenshots using only isolated synthetic metadata."""

import asyncio
import os
import tempfile
from pathlib import Path

from textual.widgets import TabbedContent

from snowdock.demo import demo_service
from snowdock.tui import Snowdock


async def capture() -> None:
    # Documentation depicts a color terminal; the app itself respects NO_COLOR.
    os.environ.pop("NO_COLOR", None)
    output = Path(__file__).resolve().parents[1] / "docs"
    with tempfile.TemporaryDirectory(prefix="snowdock-demo-", dir="/tmp") as directory:
        app = Snowdock(demo_service(Path(directory)), auto_refresh=False)
        async with app.run_test(size=(124, 36)) as pilot:
            await pilot.pause()
            app.save_screenshot("snowdock.svg", path=str(output))
            app.query_one(TabbedContent).active = "attention-tab"
            await pilot.pause()
            app.save_screenshot("attention.svg", path=str(output))
    for name in ("snowdock.svg", "attention.svg"):
        path = output / name
        path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")


if __name__ == "__main__":
    asyncio.run(capture())
