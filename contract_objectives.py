"""Pure, testable rules for optional Black Market contract bonuses.

The screenshot proves a *visible* field, not a player's identity or that a
mission actually happened. The mission bonus is shared by all paid recipients
of that submission; personal stat rankings are credited only to the uploader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

MAX_OBJECTIVES = 4

@dataclass(frozen=True)
class Metric:
    name: str
    comparison: str
    upper_limit: int
    unit: str = ''

METRICS: dict[str, Metric] = {
    'kills': Metric('Enemy kills', 'GTE', 100000),
    'stims_used': Metric('Stims used', 'GTE', 100000),
    'samples_collected': Metric('Samples collected', 'GTE', 100000),
    'shots_fired': Metric('Shots fired', 'GTE', 1000000),
    'shots_hit': Metric('Shots hit', 'GTE', 1000000),
    'stratagems_used': Metric('Stratagems used', 'GTE', 100000),
    'deaths': Metric('Deaths (at most)', 'LTE', 100000),
    'accuracy_pct': Metric('Accuracy percentage', 'GTE', 100, '%'),
    'accidentals': Metric('Accidentals (at most)', 'LTE', 100000),
    'melee_kills': Metric('Melee kills', 'GTE', 100000),
    'times_reinforcing': Metric('Reinforcements', 'GTE', 100000),
    'friendly_fire_damage': Metric('Friendly fire damage (at most)', 'LTE', 1000000),
}


def validate_bonus(*, title: str, metric: str, target: int,
                   reward: int, base_reward: int) -> dict:
    """Return canonical bonus data or raise ValueError for user input."""
    title = title.strip()
    if not 3 <= len(title) <= 80:
        raise ValueError('Bonus name must be 3–80 characters.')
    if metric not in METRICS:
        raise ValueError('Select a supported screenshot statistic.')
    if type(target) is not int or not 0 <= target <= METRICS[metric].upper_limit:
        raise ValueError('Bonus target is outside the permitted range.')
    if type(reward) is not int or not 1 <= reward < base_reward:
        raise ValueError('Each bonus must pay less than the base contract reward.')
    return {'title': title, 'stat_key': metric, 'target': target,
            'reward': reward, 'comparison': METRICS[metric].comparison}


def objective_met(row: Mapping, image_stats: dict) -> bool:
    """Absent / uncertain OCR values never qualify for automatic bonuses."""
    value = image_stats.get(row['stat_key'])
    if type(value) is not int or value < 0:
        return False
    return value >= row['target'] if row['comparison'] == 'GTE' else value <= row['target']


def requirement_text(row: Mapping) -> str:
    metric = METRICS.get(row['stat_key'])
    label = metric.name if metric else row['stat_key']
    comparator = 'at least' if row['comparison'] == 'GTE' else 'at most'
    return f'{comparator} {int(row["target"]):,} {label.lower()}'
