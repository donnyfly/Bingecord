import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord import app_commands
from test_notification_preview import bot


def test_command_names_are_general_and_link_routes_to_selected_provider(monkeypatch):
    async def run():
        commands = bot.bot.tree.get_commands()
        assert [command.name for command in commands] == ["bingecord"]
        group = commands[0]
        expected = {"link", "unlink", "source", "status", "checknow", "stats", "mapping",
                    "achievements", "challenges", "leaderboard", "server-stats", "community",
                    "watching", "random", "recommend", "style", "user-reset", "setchannel",
                    "style-server", "features", "timezone", "weekly-recap", "debug"}
        assert {command.name for command in group.commands} == expected
        assert all(command.qualified_name == "bingecord " + command.name for command in group.commands)
        payload = group.to_dict(bot.bot.tree)
        assert payload["name"] == "bingecord" and len(payload["options"]) == 23
        assert all(option["type"] == 1 for option in payload["options"])
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
