"""Next-phase selection. score = queue size + vehicle weights + 0.5 * oldest wait."""
from .models import PHASE_DIRS, VEHICLE_WEIGHT, Phase, opposite


def phase_score(queues, phase, now, cfg):
    score = 0.0
    for d in PHASE_DIRS[phase]:
        q = queues[d]
        if q:
            oldest = now - min(v.arrived_at for v in q)
            score += len(q) + sum(VEHICLE_WEIGHT[v.type] for v in q) + cfg.wait_factor * oldest
    return score


def starving_phase(queues, now, cfg):
    worst, who = cfg.max_wait_s, None
    for p in Phase:
        for d in PHASE_DIRS[p]:
            if queues[d]:
                w = now - min(v.arrived_at for v in queues[d])
                if w >= worst:
                    worst, who = w, p
    return who


def best_phase(queues, now, cfg):
    return max(Phase, key=lambda p: phase_score(queues, p, now, cfg))  # tie -> NORTH_SOUTH


def choose_next(current, queues, now, cfg, green_elapsed):
    """Return phase to switch to, or None to keep the current GREEN."""
    other = opposite(current)
    if starving_phase(queues, now, cfg) == other:
        return other                                   # starvation protection
    other_score = phase_score(queues, other, now, cfg)
    if other_score == 0:
        return None                                    # nobody waiting: don't switch
    if green_elapsed < cfg.min_green_s:
        return None                                    # minimum green
    if green_elapsed >= cfg.green_s:
        return other                                   # max green reached
    if other_score > phase_score(queues, current, now, cfg) * cfg.hysteresis:
        return other
    return None
