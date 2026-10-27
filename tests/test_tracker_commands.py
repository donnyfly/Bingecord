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
        expected = {"help", "link", "unlink", "source", "status", "checknow", "stats", "mapping",
                    "achievements", "challenges", "leaderboard", "server-stats", "community",
                    "watching", "random", "recommend", "style", "user-reset", "setchannel",
                    "style-server", "features", "timezone", "weekly-recap", "debug", "rebuild"}
        assert {command.name for command in group.commands} == expected
        assert all(command.qualified_name == "bingecord " + command.name for command in group.commands)
        payload = group.to_dict(bot.bot.tree)
        assert payload["name"] == "bingecord" and len(payload["options"]) == 25
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


def test_help_is_private_complete_and_available_with_features_disabled(monkeypatch):
    async def run():
        import discord
        assert bot.bot.activity.name == "/bingecord help"
        assert bot.bot.activity.type == discord.ActivityType.playing
        forbidden = AsyncMock(side_effect=AssertionError("Help must not need account or feature data"))
        monkeypatch.setattr(bot.storage, "get_features", forbidden)
        i = SimpleNamespace(guild_id=1, data={"name":"bingecord","options":[{"type":1,"name":"help"}]},
                            response=SimpleNamespace(send_message=AsyncMock()))
        assert await bot.bot.tree.interaction_check(i)
        await bot.bingecord_help.callback(i)
        kwargs = i.response.send_message.await_args.kwargs
        assert kwargs["ephemeral"] is True
        embed = kwargs["embed"]
        assert len(embed) < 6000
        assert all(len(field.value) <= 1024 for field in embed.fields)
        guide = embed.description + " " + " ".join(field.value for field in embed.fields)
        assert all("`" + c.name + "`" in guide or "/bingecord " + c.name in guide
                   for c in bot.bingecord_commands.commands if c.name != "help")
        forbidden.assert_not_awaited()
    asyncio.run(run())
