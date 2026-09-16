"""Trusted deployment-only, once-per-approval resident activation. No HTTP route."""
import httpx
from decimal import Decimal, InvalidOperation
from db import setting, audit


def cap_checks(data, credits_only=False):
    def amount(name):
        try:
            value = Decimal(str(data.get(name)))
            return value if value.is_finite() else Decimal('-1')
        except (InvalidOperation, ValueError, TypeError):
            return Decimal('-1')
    return {
        'limit_at_most_25': 0 < amount('limit') <= 25,
        'nonresetting': data.get('limit_reset') in (None, ''),
        'byok_protected': data.get('include_byok_in_limit') is True or (
            credits_only and amount('byok_usage') == 0),
        'credit_remaining': amount('limit_remaining') > 0,
    }


async def activate(residents, approval, owner_ready):
    # An environment variable is an infrastructure-operator action, never forum input.
    if not approval:
        return {'status': 'not_requested'}
    marker = 'resident_activation:' + approval
    with residents.db.read() as c:
        if setting(c, marker) == 'consumed':
            return {'status': 'already_consumed'}
    if not owner_ready or not residents.key:
        return {'status': 'owner_or_key_missing'}
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False,
                                    headers={'Authorization': 'Bearer ' + residents.key}) as client:
            data = (await residents.json_request(client, 'GET', 'key')).get('data', {})
        checks = cap_checks(data, residents.credits_only)
    except Exception:
        # Do not log exception strings, headers, provider responses, or key metadata.
        return {'status': 'provider_check_failed'}
    if not all(checks.values()):
        return {'status': 'provider_cap_blocked', **checks}
    if residents.dry_run:
        return {'status': 'ready_but_dry_run', **checks}
    residents.db.backup()
    with residents.db.tx() as c:
        if setting(c, marker) == 'consumed':
            return {'status': 'already_consumed'}
        if c.execute("SELECT 1 FROM aq_inference WHERE state IN ('RESERVED','UNCERTAIN')").fetchone():
            return {'status': 'unresolved_cost'}
        for key, value in [('paused', 'false'), ('inference_enabled', 'true'),
                           ('scheduler_interval_seconds', '900'), (marker, 'consumed')]:
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, value))
        audit(c, 'owner:deployment', 'resident_activation',
              {'approval': approval, 'maximum_usd': 25, 'interval_seconds': 900, 'owner_confirmed_no_byok': residents.credits_only})
    return {'status': 'activated', **checks}
