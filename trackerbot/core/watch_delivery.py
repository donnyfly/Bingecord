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
    users = list(dict.fromkeys(users))
    names = [f'<@{user}>' for user in users[:5]]
    if len(users)>5:
        names.append(f'{len(users)-5:,} others')
    if not names:
        return ''
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
    providers=list(dict.fromkeys((item.embed.footer.text or '').split(' · ')[-1] for item in activities))
    if providers:embed.set_footer(text='Watched Together · '+ ' + '.join(providers))
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
        cycle=current_cycle.get()
        if cycle is not None:
            # Submit the whole SIMKL batch together; never acknowledge a post
            # merely because it has been queued for another provider to join.
            commits=[item.commit for item in self.activities]
            accepted=await cycle.submit(self.activities) if self.activities else []
            for item,commit,sent in zip(self.activities,commits,accepted):
                item.delivered=False
                if sent:
                    await commit();item.delivered=True
            for finalize in self.finalizers:await finalize()
            return
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

# One coordinator per complete multi-provider polling cycle. Waiting for Discord
# delivery keeps every provider's existing post-before-acknowledge contract.
import asyncio
from contextvars import ContextVar

current_cycle = ContextVar('watch_delivery_cycle', default=None)


def watch_key(kind, ids, season=None, episodes=(), provider=None, native=None):
    anchor=next(((name,str(ids[name])) for name in ('tmdb','tvdb','imdb','mal') if ids.get(name)),None)
    if anchor is None:anchor=(provider or 'unknown',str(native))
    return (kind,anchor,season,tuple(episodes)) if kind=='episode' else (kind,anchor)


class WatchCycle:
    def __init__(self,sender):
        self.sender=sender
        self.scheduled=0
        self.running=set()
        self.blocked=set()
        self.pending=[]
        self.owners={}
        self.changed=asyncio.Event()

    def run(self,awaitable):
        # Register synchronously, before the new coroutine gets CPU time.
        self.scheduled+=1;self.changed.set()
        async def worker():
            task=asyncio.current_task();self.scheduled-=1
            self.running.add(task);self.changed.set()
            try:return await awaitable
            finally:self.running.discard(task);self.blocked.discard(task);self.changed.set()
        return worker()

    async def join(self,awaitables):
        task=asyncio.current_task()
        workers=[self.run(a) for a in awaitables]
        self.blocked.add(task);self.changed.set()
        try:return await asyncio.gather(*workers)
        finally:self.blocked.discard(task);self.changed.set()

    async def submit(self,activities):
        task=asyncio.current_task()
        futures=[]
        for activity in activities:
            future=asyncio.get_running_loop().create_future()
            self.pending.append((activity,future));futures.append(future)
            self.owners[future]=task
        self.blocked.add(task);self.changed.set()
        try:return await asyncio.gather(*futures)
        finally:self.blocked.discard(task);self.changed.set()

    def finish(self,future,sent):
        if not future.done():future.set_result(sent)
        owner=self.owners.get(future)
        if owner and all(f.done() for f,t in self.owners.items() if t is owner):
            self.blocked.discard(owner)
        self.changed.set()

    async def flush(self):
        pending,self.pending=self.pending,[]
        probe=WatchBatch(self.sender)
        for activity,_ in pending:probe.add(activity)
        matched={id(item) for group in probe.groups() if len(group)>1 for item in group}
        if matched:
            # Release matched participants first so their next native activity
            # can join another SIMKL item already waiting in the same cycle.
            self.pending=[pair for pair in pending if id(pair[0]) not in matched]
            pending=[pair for pair in pending if id(pair[0]) in matched]
        batch=WatchBatch(self.sender)
        for activity,future in pending:
            async def accept(future=future):
                self.finish(future,True)
            # Original commits stay in their provider, after submit returns.
            activity.commit=accept
            batch.add(activity)
        token=current_cycle.set(None)
        try:await batch.deliver()
        finally:current_cycle.reset(token)
        for _,future in pending:
            self.finish(future,False)

    async def execute(self,awaitables):
        token=current_cycle.set(self)
        roots=[asyncio.create_task(a) for a in awaitables]
        for root in roots:root.add_done_callback(lambda _:self.changed.set())
        try:
            while not all(root.done() for root in roots):
                self.changed.clear()
                # Let provider wrappers register their leaf workers first.
                await asyncio.sleep(0)
                if self.pending and not self.scheduled and self.running and self.running<=self.blocked:
                    await self.flush()
                elif not all(root.done() for root in roots):
                    await self.changed.wait()
            return await asyncio.gather(*roots)
        finally:
            for root in roots:
                if not root.done():root.cancel()
            await asyncio.gather(*roots,return_exceptions=True)
            for _,future in self.pending:
                if not future.done():future.cancel()
            current_cycle.reset(token)


async def deliver_watch(sender,activity):
    cycle=current_cycle.get()
    if cycle is None:return await sender(activity.channel,activity.embed,'watch')
    return (await cycle.submit([activity]))[0]
