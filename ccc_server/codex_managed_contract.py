"""Immutable native goal classes shared by proof validation and worker reads.

Keep this leaf free of generated classes, execution and server imports so the
guarded worker can validate the fixed control protocol without a proof loader.
"""

GOAL_STATES = frozenset({'active', 'paused', 'blocked', 'usageLimited', 'budgetLimited', 'complete'})
