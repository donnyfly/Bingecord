"""Collect a poll's watch notifications before delivering and acknowledging them."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
import logging

log = logging.getLogger('simkl-bot')


@dataclass
class WatchActivity:
    guild: str
    user: str
    scope: str
    channel: object
    key: tuple
    times: tuple
    embed: object
    subject: str
    commit: object
    started: bool = False
    rewatched: bool = False
    movie: bool = False
    count: int = 1
    delivered: bool = False


def mentions(users):
    names = [f'<@{user}>' for user in users]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def together_embed(activities):
    first = activities[0]
    embed = first.embed.copy()
    embed.set_author(name='Watched Together')
    if not embed.thumbnail or not embed.thumbnail.url:
        for activity in activities[1:]:
            if activity.embed.thumbnail and activity.embed.thumbnail.url:
                embed.set_thumbnail(url=activity.embed.thumbnail.url)
                break
    all_rewatched = all(item.rewatched for item in activities)
    verb = 'rewatched' if all_rewatched else 'watched'
    lines = [f'{mentions([item.user for item in activities])} {verb} {first.subject} together']
    # Keep the episode title and ratings from the ordinary watch embed.
    lines.extend(line for line in (embed.description or '').splitlines()[1:]
                 if line.strip() and not line.startswith('🆕'))
    starters = [item.user for item in activities if item.started]
    if starters:
        if len(starters) == len(activities):
            who = 'Both' if len(starters) == 2 else 'Everyone'
            lines.extend(['', f'🆕 {who} started this series.'])
        else:
            lines.extend(['', f'🆕 {mentions(starters)} started this series.'])
    rewatchers = [item.user for item in activities if item.rewatched]
    if rewatchers and not all_rewatched:
        noun = 'movie' if first.movie else ('episodes' if first.count > 1 else 'episode')
        lines.append(f'🔁 {mentions(rewatchers)} rewatched {"these" if noun == "episodes" else "this"} {noun}.')
    embed.description = '\n'.join(lines)
    return embed


class WatchBatch:
    def __init__(self, sender, window_minutes=30):
        self.sender = sender
        self.window = timedelta(minutes=window_minutes)
        self.activities = []
        self.finalizers = []

    def add(self, activity):
        self.activities.append(activity)

    def failed(self, guild, user, scope):
        return any(not item.delivered for item in self.activities
                   if (item.guild, item.user, item.scope) == (str(guild), str(user), scope))

    def groups(self):
        buckets = defaultdict(list)
        for item in self.activities:
            buckets[(item.guild, str(item.channel.id), item.key)].append(item)
        for items in buckets.values():
            clusters = []
            for item in sorted(items, key=lambda x: (x.times, x.user)):
                for cluster in clusters:
                    if any(other.user == item.user for other in cluster):
                        continue
                    # Compare every episode's timestamp; avoid chained windows
                    # that would combine users more than 30 minutes apart.
                    if all(len(item.times) == len(other.times) and
                           all(abs(a-b) <= self.window for a,b in zip(item.times, other.times))
                           for other in cluster):
                        candidate = cluster + [item]
                        # Discord's description/total embed limits also bound
                        # the number of participants shown in a single post.
                        embed = together_embed(candidate)
                        if len(embed.description) <= 4096 and len(embed) <= 6000:
                            cluster.append(item)
                            break
                else:
                    clusters.append([item])
            yield from clusters

    async def deliver(self):
        for group in self.groups():
            embed = together_embed(group) if len(group) > 1 else group[0].embed
            try:
                sent = await self.sender(group[0].channel, embed, 'watch')
            except Exception:
                log.exception('Watch delivery failed.')
                sent = False
            if not sent:
                continue
            for item in group:
                try:
                    await item.commit()
                    item.delivered = True
                except Exception:
                    log.exception('Could not acknowledge watch activity for user %s.', item.user)
        for finalize in self.finalizers:
            try:
                await finalize()
            except Exception:
                log.exception('Could not finalize polling target.')
