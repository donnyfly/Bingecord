import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord import app_commands
from test_notification_preview import bot


def test_command_names_are_general_and_link_routes_to_selected_provider(monkeypatch):
    async def run():
        names = {command.name for command in bot.bot.tree.get_commands()}
        assert "tracker-link" in names
        assert "tracker-unlink" in names
        assert "tracker-setchannel" in names
        assert "tracker-checknow" in names
        assert not any(name.startswith(("simkl-", "wetrakr-")) for name in names)
        simkl = AsyncMock()
        wetrakr = AsyncMock()
        monkeypatch.setattr(bot, "simkl_link", simkl)
        monkeypatch.setattr(bot, "wetrakr_link", wetrakr)
        interaction = SimpleNamespace()
        await bot.tracker_link.callback(interaction, app_commands.Choice(name="SIMKL", value="simkl"))
        await bot.tracker_link.callback(interaction, app_commands.Choice(name="WeTrakr", value="wetrakr"))
        simkl.assert_awaited_once_with(interaction)
        wetrakr.assert_awaited_once_with(interaction)
    asyncio.run(run())
