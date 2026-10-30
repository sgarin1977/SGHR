from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from services.construction.construction_address_matching import (
    ConstructionAddressCandidate as Candidate, normalize_construction_address as normalize,
    rank_construction_address_suggestions as rank,
)


@pytest.mark.parametrize('raw,expected',[
    ('  RUA   DAS FLORES, １２ / １  ','rua das flores 12 1'),
    ('ВУЛ. ЛЕСІ УКРАЇНКИ — 7','вул лесі українки 7'),
    ('Straße 5','strasse 5'),('Rua Sa\u0303o Jose\u0301 8','rua são josé 8'),
    (None,''),('... / -',''),
])
def test_normalization_handles_unicode_case_spacing_and_component_boundaries(raw,expected):
    assert normalize(raw)==expected
    assert normalize(expected)==expected
    assert normalize('12/1')!=normalize('121')


def candidate(tenant,address,**changes):
    return Candidate(**dict(dict(entity_type='project',entity_id=uuid4(),tenant_id=tenant,
        label='Existing project',address_raw=address),**changes))


def test_exact_prefix_token_and_fuzzy_have_the_approved_order():
    tenant=uuid4()
    exact=candidate(tenant,'RUA DAS FLORES, 12')
    prefix=candidate(tenant,'Rua das Flores 12 Lisboa Portugal')
    fuzzy=candidate(tenant,'Rua das Florez 12')
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[fuzzy,prefix,exact])
    assert [r.entity_id for r in result]==[exact.entity_id,prefix.entity_id,fuzzy.entity_id]
    assert [r.match_kind for r in result]==['exact','prefix_token','fuzzy']


def test_token_match_handles_reordered_tokens_and_does_not_ignore_repetition():
    tenant=uuid4()
    row=candidate(tenant,'12 Rua das Flores')
    assert rank(tenant_id=tenant,query='Flores 12',candidates=[row])[0].match_kind=='prefix_token'
    assert rank(tenant_id=tenant,query='Flores 12 12',candidates=[row])==()


def test_only_current_tenant_eligible_undeleted_candidates_participate():
    tenant=uuid4();row=candidate(tenant,'Rua das Flores 12')
    rows=[replace(row,tenant_id=uuid4()),replace(row,eligible=False),
        replace(row,deleted_at=datetime.now(timezone.utc)),row]
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=rows)
    assert len(result)==1 and result[0].entity_id==row.entity_id


def test_existing_normalized_address_is_used_with_raw_fallback_and_original_text_is_retained():
    tenant=uuid4()
    row=candidate(tenant,'Original entered address',address_normalized='Rua das Flores 12')
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[row])
    assert result[0].match_kind=='exact' and result[0].address_raw=='Original entered address'
    row=replace(row,address_raw='Rua das Flores 12',address_normalized='')
    assert rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[row])[0].match_kind=='exact'


def test_maximum_ten_results_have_stable_order_independent_of_input_order():
    tenant=uuid4()
    rows=[candidate(tenant,'Rua das Flores 12',entity_id=UUID(int=i)) for i in range(1,16)]
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=rows)
    assert len(result)==10
    assert result==rank(tenant_id=tenant,query='Rua das Flores 12',candidates=reversed(rows))
    assert [r.entity_id for r in result]==[UUID(int=i) for i in range(1,11)]


def test_duplicate_entities_keep_only_their_best_address_match():
    tenant=uuid4();row=candidate(tenant,'Rua das Florez 12')
    exact=replace(row,address_raw='Rua das Flores 12')
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[row,exact,row])
    assert len(result)==1 and result[0].match_kind=='exact'
    assert rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[exact,row])==result


def test_client_and_project_ids_do_not_collapse_into_one_entity():
    tenant=uuid4();row=candidate(tenant,'Rua das Flores 12')
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[row,replace(row,entity_type='client')])
    assert {r.entity_type for r in result}=={'client','project'}


def test_unrelated_addresses_and_empty_input_return_no_suggestion():
    tenant=uuid4();row=candidate(tenant,'Lisboa Rua das Flores 12')
    assert rank(tenant_id=tenant,query='Київ Хрещатик 99',candidates=[row])==()
    assert rank(tenant_id=tenant,query=None,candidates=[row])==()
    assert rank(tenant_id=tenant,query='... ',candidates=[row])==()


def test_matching_is_read_only_and_result_does_not_link_or_replace_input():
    tenant=uuid4();row=candidate(tenant,'  Rua das Flores, 12  ');rows=[row]
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=rows)
    assert rows==[row] and row.address_raw=='  Rua das Flores, 12  '
    assert result[0].address_raw==row.address_raw
    with pytest.raises(FrozenInstanceError):result[0].address_raw='changed'
    assert not hasattr(result[0],'linked_user_id') and not hasattr(result[0],'client_id')


@pytest.mark.parametrize('limit',[0,11,True,'10',None])
def test_invalid_limits_cannot_exceed_ten(limit):
    with pytest.raises(ValueError):rank(tenant_id=uuid4(),query='Rua',candidates=[],limit=limit)


def test_tenant_context_is_required_and_identifiers_are_not_coerced():
    with pytest.raises(ValueError):rank(tenant_id=str(uuid4()),query='Rua',candidates=[])
    with pytest.raises(ValueError):candidate(uuid4(),'Rua',entity_id='caller-id')


def test_fuzzy_threshold_is_internal_and_never_reorders_above_exact_matches():
    tenant=uuid4()
    exact=candidate(tenant,'Rua das Flores 12')
    typo=candidate(tenant,'Rua das Florez 12')
    result=rank(tenant_id=tenant,query='Rua das Flores 12',candidates=[typo,exact],limit=1)
    assert len(result)==1 and result[0].entity_id==exact.entity_id
