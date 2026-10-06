"""Independent provider configuration must not require a SIMKL application."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('provider', ['WETRAKR_API_KEY', 'MDBLIST_CLIENT_ID'])
def test_startup_without_simkl(provider):
    env = {**os.environ, 'DISCORD_BOT_TOKEN': 'test-token', 'TMDB_API_KEY': 'test-key',
           'SIMKL_CLIENT_ID': ' ', 'WETRAKR_API_KEY': '', 'MDBLIST_CLIENT_ID': '',
           'MDBLIST_API_KEY': '', provider: 'test-client'}
    script = '''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import trackerbot.bot as app
from discord import app_commands

assert app.SIMKL_CLIENT_ID == ''
async def run():
    assert await app.poll_all() == 0
    interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=42),
                                 response=SimpleNamespace(send_message=AsyncMock()))
    await app.simkl_link(interaction)
    assert 'not configured' in interaction.response.send_message.call_args.args[0]
    interaction.response.send_message.reset_mock()
    await app.tracker_source.callback(interaction, app_commands.Choice(name='SIMKL', value='simkl'))
    assert 'not configured' in interaction.response.send_message.call_args.args[0]
    try:
        await app.provider_registry.get('simkl').link('42')
    except ValueError as exc:
        assert 'not configured' in str(exc)
    else:
        raise AssertionError('Disabled SIMKL must reject account reads')
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], env=env,
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
