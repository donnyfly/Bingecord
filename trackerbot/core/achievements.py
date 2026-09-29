"""Achievement definitions for SIMKLTrackerBot.

Achievements are intentionally challenging so higher tiers feel meaningful
rather than being awarded for routine activity.
"""

ACHIEVEMENTS = {
    "first_watch": {
        "name": "First Watch",
        "emoji": "🎬",
        "description": "Record your first watch.",
        "category": "total",
        "threshold": 1,
        "xp": 100,
    },
    "episodes_50": {
        "name": "Seasoned Watcher",
        "emoji": "📺",
        "description": "Watch 50 episodes.",
        "category": "episodes",
        "threshold": 50,
        "xp": 250,
    },
    "episodes_250": {
        "name": "Binge Master",
        "emoji": "📚",
        "description": "Watch 250 episodes.",
        "category": "episodes",
        "threshold": 250,
        "xp": 500,
    },
    "episodes_1000": {
        "name": "Episode Veteran",
        "emoji": "🏅",
        "description": "Watch 1,000 episodes.",
        "category": "episodes",
        "threshold": 1000,
        "xp": 1000,
    },
    "episodes_2500": {
        "name": "Episode Legend",
        "emoji": "👑",
        "description": "Watch 2,500 episodes.",
        "category": "episodes",
        "threshold": 2500,
        "xp": 2000,
    },
    "movies_25": {
        "name": "Movie Regular",
        "emoji": "🍿",
        "description": "Watch 25 movies.",
        "category": "movies",
        "threshold": 25,
        "xp": 250,
    },
    "movies_100": {
        "name": "Movie Buff",
        "emoji": "🎞️",
        "description": "Watch 100 movies.",
        "category": "movies",
        "threshold": 100,
        "xp": 500,
    },
    "movies_250": {
        "name": "Cinephile",
        "emoji": "🎥",
        "description": "Watch 250 movies.",
        "category": "movies",
        "threshold": 250,
        "xp": 1000,
    },
    "anime_250": {
        "name": "Anime Devotee",
        "emoji": "🌸",
        "description": "Watch 250 anime episodes.",
        "category": "anime_episodes",
        "threshold": 250,
        "xp": 500,
    },
    "anime_1000": {
        "name": "Anime Veteran",
        "emoji": "🌟",
        "description": "Watch 1,000 anime episodes.",
        "category": "anime_episodes",
        "threshold": 1000,
        "xp": 1000,
    },
    "anime_2500": {
        "name": "Anime Legend",
        "emoji": "💮",
        "description": "Watch 2,500 anime episodes.",
        "category": "anime_episodes",
        "threshold": 2500,
        "xp": 2000,
    },
    "total_250": {
        "name": "Dedicated Watcher",
        "emoji": "🏆",
        "description": "Watch 250 total items.",
        "category": "total",
        "threshold": 250,
        "xp": 250,
    },
    "total_1000": {
        "name": "Committed Watcher",
        "emoji": "💎",
        "description": "Watch 1,000 total items.",
        "category": "total",
        "threshold": 1000,
        "xp": 750,
    },
    "total_2500": {
        "name": "Elite Watcher",
        "emoji": "🥇",
        "description": "Watch 2,500 total items.",
        "category": "total",
        "threshold": 2500,
        "xp": 1500,
    },
    "total_5000": {
        "name": "Legendary",
        "emoji": "👑",
        "description": "Watch 5,000 total items.",
        "category": "total",
        "threshold": 5000,
        "xp": 3000,
    },
    "streak_30": {
        "name": "Monthly Dedication",
        "emoji": "🔥",
        "description": "Maintain a 30-day watch streak.",
        "category": "streak",
        "threshold": 30,
        "xp": 500,
    },
    "streak_100": {
        "name": "Unstoppable",
        "emoji": "⚡",
        "description": "Maintain a 100-day watch streak.",
        "category": "streak",
        "threshold": 100,
        "xp": 1500,
    },
    "streak_365": {
        "name": "Year-Round Watcher",
        "emoji": "🌠",
        "description": "Maintain a 365-day watch streak.",
        "category": "streak",
        "threshold": 365,
        "xp": 3000,
    },
}

# Milestones use reconciled watch events, so removing watches can relock them.
for key, name, emoji, description, category, threshold, xp in (
    ("episodes_10", "Pilot Season", "📺", "Watch 10 episodes.", "episodes", 10, 150),
    ("episodes_100", "Hundred Episode Club", "📚", "Watch 100 episodes.", "episodes", 100, 350),
    ("episodes_500", "Marathon Viewer", "🏃", "Watch 500 episodes.", "episodes", 500, 750),
    ("episodes_5000", "Endless Episodes", "🌌", "Watch 5,000 episodes.", "episodes", 5000, 3500),
    ("movies_1", "Opening Night", "🍿", "Watch your first movie.", "movies", 1, 100),
    ("movies_10", "Double Feature Fan", "🎟️", "Watch 10 movies.", "movies", 10, 200),
    ("movies_50", "Box Office Regular", "🎬", "Watch 50 movies.", "movies", 50, 350),
    ("movies_500", "Silver Screen Legend", "🎥", "Watch 500 movies.", "movies", 500, 2000),
    ("anime_10", "First Arc", "🌸", "Watch 10 anime episodes.", "anime_episodes", 10, 150),
    ("anime_100", "Arc Collector", "⛩️", "Watch 100 anime episodes.", "anime_episodes", 100, 350),
    ("anime_500", "Season Traveller", "🎏", "Watch 500 anime episodes.", "anime_episodes", 500, 750),
    ("anime_movies_1", "Anime Premiere", "🎞️", "Watch an anime movie.", "anime_movies", 1, 150),
    ("anime_movies_10", "Anime Film Night", "🌙", "Watch 10 anime movies.", "anime_movies", 10, 350),
    ("anime_movies_25", "Anime Film Collector", "🏮", "Watch 25 anime movies.", "anime_movies", 25, 600),
    ("titles_25", "Explorer", "🧭", "Watch 25 different titles.", "unique_titles", 25, 300),
    ("titles_100", "Media Explorer", "🗺️", "Watch 100 different titles.", "unique_titles", 100, 750),
    ("active_days_30", "Thirty Days of Stories", "🗓️", "Watch on 30 different days.", "active_days", 30, 500),
    ("active_days_100", "Century of Days", "📆", "Watch on 100 different days.", "active_days", 100, 1250),
    ("streak_7", "One Week Streak", "🔥", "Watch on 7 consecutive days.", "streak", 7, 250),
    ("streak_14", "Two Week Streak", "⚡", "Watch on 14 consecutive days.", "streak", 14, 350),
):
    ACHIEVEMENTS[key] = {"name": name, "emoji": emoji, "description": description,
                         "category": category, "threshold": threshold, "xp": xp}


def get_achievement(achievement_id):
    return ACHIEVEMENTS.get(achievement_id)


def all_achievements():
    return list(ACHIEVEMENTS.items())
