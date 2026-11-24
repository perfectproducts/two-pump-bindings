# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""One fault-code interpretation for every consumer; no rendering, USD or network imports.

The alarm *decision* is made once, upstream of every consumer (source-owned alarm or edge
rule). The view's ``fault_codes`` table, delivered in the same snapshot as catalog and values,
gives each alarmed metric a stable code, name, severity and text. Consumers call these helpers
instead of interpreting alarms themselves, so console, HTML and USD agree by construction.
"""
from .config import source_key


def table(view):
    return {entry['metric']: entry for entry in (view or {}).get('fault_codes', [])}


def active(rows, view):
    """Fault entries whose metric is currently in ALARM, one per (source, id, code)."""
    codes = table(view)
    result = []
    for row in rows:
        for display in row['displays']:
            entry = codes.get(display['spec']['metric'])
            if entry is None or display['state'] != 'alarm':
                continue
            sample = display['sample']
            item = dict(entry, source=row['binding']['source'], id=row['binding']['id'],
                        label=row['binding']['label'], observed=sample.observed if sample else None,
                        event_id=sample.event_id if sample else None)
            if (item['source'], item['id'], item['code']) not in {(r['source'], r['id'], r['code']) for r in result}:
                result.append(item)
    return result


def describe(view, code):
    for entry in (view or {}).get('fault_codes', []):
        if entry['code'] == code:
            return entry
    return None


def sample_state(sample):
    """Same rule as ViewModel.state(), applied to one accepted sample."""
    if sample.alarm is True:
        return 'alarm'
    if sample.value is None or (sample.alarm_expected and sample.alarm is None):
        return 'unknown'
    return 'normal'


class Tracker:
    """Fault transitions from the accepted per-sample stream (not from coalesced snapshots).

    Every consumer that feeds its own Client's ``drain_samples()`` here reports the same
    transitions, with the same occurrence IDs and observation times.
    """
    def __init__(self):
        self.state = {}

    def feed(self, samples, view):
        codes = table(view)
        result = []
        for sample in samples:
            entry = codes.get(sample.metric)
            if entry is None:
                continue
            key = sample.source, sample.source_id, entry['code']
            new, old = sample_state(sample), self.state.get(key)
            self.state[key] = new
            if new == old or (old is None and new != 'alarm'):
                continue
            result.append(dict(entry, source=sample.source, id=sample.source_id, before=old or 'none', after=new,
                               observed=sample.observed, value=sample.value, unit=sample.unit, event_id=sample.event_id))
        return result


def format_value(entry):
    return '-' if entry['value'] is None else f"{entry['value']:g} {entry['unit']}".strip()
