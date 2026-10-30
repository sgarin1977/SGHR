"""CON-C015 matching rules; no queries, geocoding, writes or automatic linking.

The caller must authorize and supply eligible client/project candidates. This
module does not decide where client addresses come from or which project
lifecycle statuses qualify. Tenant filtering here is an additional safeguard.
Scores and the fuzzy cutoff are internal search tuning, not persisted address
data or a condition for saving a project. Original address text stays intact.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import unicodedata
from uuid import UUID


MAX_ADDRESS_SUGGESTIONS = 10
FUZZY_MIN_SCORE = 800
_TIERS = {'exact': 0, 'prefix_token': 1, 'fuzzy': 2}


def normalize_construction_address(value: str | None) -> str:
    if value is None: return ''
    if not isinstance(value, str): raise ValueError('Address must be text.')
    text = unicodedata.normalize('NFKC', value).casefold()
    # Retain letters, numbers and combining marks from every supported script.
    # Punctuation separates components: 12/1 must not collapse into 121.
    return ' '.join(''.join(character if unicodedata.category(character)[0] in 'LNM'
        else ' ' for character in text).split())


@dataclass(frozen=True)
class ConstructionAddressCandidate:
    entity_type: str
    entity_id: UUID
    tenant_id: UUID
    label: str
    address_raw: str
    address_normalized: str | None = None
    eligible: bool = True
    deleted_at: datetime | None = None

    def __post_init__(self):
        if (self.entity_type not in ('client', 'project')
            or not isinstance(self.entity_id, UUID) or not isinstance(self.tenant_id, UUID)
            or not isinstance(self.label, str) or not isinstance(self.address_raw, str)
            or (self.address_normalized is not None and not isinstance(self.address_normalized, str))
            or type(self.eligible) is not bool): raise ValueError('Address candidate is invalid.')


@dataclass(frozen=True)
class ConstructionAddressSuggestion:
    entity_type: str
    entity_id: UUID
    label: str
    address_raw: str
    match_kind: str
    score: int


def _match(query, address):
    if not address: return None
    if query == address: return 'exact', 1000
    score = round(SequenceMatcher(None, query, address, autojunk=False).ratio()*1000)
    tokens, available = Counter(query.split()), Counter(address.split())
    if address.startswith(query) or all(count <= available[token] for token, count in tokens.items()):
        return 'prefix_token', score
    if score >= FUZZY_MIN_SCORE: return 'fuzzy', score
    return None


def select_construction_address_matches(*, tenant_id, query, candidates, limit=MAX_ADDRESS_SUGGESTIONS):
    """Return candidate/suggestion pairs for bounded streaming; never link an entity."""
    if not isinstance(tenant_id, UUID): raise ValueError('Trusted tenant context is required.')
    if type(limit) is not int or not 1 <= limit <= MAX_ADDRESS_SUGGESTIONS:
        raise ValueError('Address suggestion limit must be between 1 and 10.')
    normalized_query = normalize_construction_address(query)
    if not normalized_query: return ()
    best = {}
    for candidate in candidates:
        if not isinstance(candidate, ConstructionAddressCandidate): raise ValueError('Address candidate is invalid.')
        if candidate.tenant_id != tenant_id or not candidate.eligible or candidate.deleted_at is not None: continue
        address = (normalize_construction_address(candidate.address_normalized)
            or normalize_construction_address(candidate.address_raw))
        matched = _match(normalized_query, address)
        if matched is None: continue
        kind, score = matched
        item = ConstructionAddressSuggestion(candidate.entity_type, candidate.entity_id, candidate.label,
            candidate.address_raw, kind, score)
        # A duplicate entity can occur in a future read projection. Pick its
        # best match deterministically without establishing any new relation.
        key = (_TIERS[kind], -score, address, candidate.entity_type, str(candidate.entity_id),
            candidate.label, candidate.address_raw)
        identity = candidate.entity_type, candidate.entity_id
        if identity not in best or key < best[identity][0]: best[identity] = key, candidate, item
    return tuple((candidate, item) for _, candidate, item in sorted(best.values(), key=lambda pair: pair[0])[:limit])


def rank_construction_address_suggestions(*, tenant_id, query, candidates, limit=MAX_ADDRESS_SUGGESTIONS):
    """Rank already-authorized candidates; never select or link an entity."""
    return tuple(item for _, item in select_construction_address_matches(
        tenant_id=tenant_id, query=query, candidates=candidates, limit=limit))
