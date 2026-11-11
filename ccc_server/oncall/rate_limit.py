"""A bounded owner-alert budget that survives wall-clock rollback."""


def load(store, now, cap):
    saved = store.kv_get('owner_budget')
    if not isinstance(saved, dict):
        # Upgrade conservatively: retained successful alerts occupy a full hour.
        entries = {str(i): 3600 for i in store.recent_alert_ids(now - 3600, cap)}
    else:
        elapsed = max(0, now - saved['at'])
        entries = {key: remaining - elapsed for key, remaining in saved['entries'].items()
                   if remaining > elapsed}
    budget = {'at': now, 'entries': entries}
    store.kv_set('owner_budget', budget)
    return budget


def record(store, budget, alert_id):
    budget['entries'][str(alert_id)] = 3600
    store.kv_set('owner_budget', budget)
