"""Command-line entry point; application code lives in trackerbot.bot."""

if __name__ == "__main__":
    from trackerbot.bot import DISCORD_BOT_TOKEN, bot

    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
else:
    # Keep ``import bot`` compatible with existing tools and test fixtures.
    import importlib
    import sys

    sys.modules[__name__] = importlib.import_module("trackerbot.bot")
