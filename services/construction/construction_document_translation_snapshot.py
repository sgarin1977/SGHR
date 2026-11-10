"""Version-bound document text preparation, following rev.12 section 19.4.

The document workflow must authorize the operation, supply canonical sources
and tenant-filtered cache records, recheck freshness after provider work, and
persist snapshot/cache/audit atomically. This component grants no access.

No database, publication, numbering, financial calculation or commit here.
Published snapshots must be stored by the document lifecycle; this module only
prepares immutable canonical JSON and pending automatic cache records.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID

from services.construction.construction_content_translation import (
    ConstructionContentTranslator,
    ContentTranslationSource,
    ContentTranslationRecord,
    ContentTranslationContractError as ContractError,
    ContentTranslationStatus as Status,
    ContentFieldKind,
    PreparedContentTranslation,
)
from services.construction.construction_document_language import (
    ConstructionDocumentLanguagePlan,
    validate_document_languages,
)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)


def _logical(value):
    return value.entity_type, value.entity_id, value.field_name


@dataclass(frozen=True)
class DocumentTranslationSnapshot:
    canonical_json: str

    def __post_init__(self):
        try:
            if not isinstance(self.canonical_json, str):
                raise ValueError()
            value = json.loads(self.canonical_json)
            if (
                not isinstance(value, dict)
                or set(value) != {
                    "schema_version", "tenant_id",
                    "document_version_id", "document_version",
                    "document_languages", "fields",
                }
                or _json(value) != self.canonical_json
            ):
                raise ValueError()
        except (ValueError, TypeError):
            raise ContractError("Canonical document text snapshot is required.") from None

    @property
    def content_hash(self):
        return sha256(self.canonical_json.encode("utf-8")).hexdigest()

    def payload(self):
        # Each call creates an independent mutable projection for a renderer.
        return json.loads(self.canonical_json)


@dataclass(frozen=True)
class PreparedDocumentTranslationSnapshot:
    snapshot: DocumentTranslationSnapshot
    pending: tuple[ContentTranslationRecord, ...]


class ConstructionDocumentTranslationPreparer:
    def __init__(self, *, translator):
        if not isinstance(translator, ConstructionContentTranslator):
            raise ValueError("The shared content translator is required.")
        self.translator = translator

    @staticmethod
    def _validate_inputs(
        language_plan, document_version_id, document_version, sources, records,
    ):
        if (
            not isinstance(language_plan, ConstructionDocumentLanguagePlan)
            or not isinstance(document_version_id, UUID)
            or type(document_version) is not int
            or not 1 <= document_version <= 2**63 - 1
            or not isinstance(sources, tuple) or not sources
            or any(not isinstance(source, ContentTranslationSource) for source in sources)
            or not isinstance(records, tuple)
            or any(not isinstance(record, ContentTranslationRecord) for record in records)
        ):
            raise ContractError(
                "Typed document context, canonical sources and cache records are required.")
        validate_document_languages(language_plan.document_languages)
        identities = {_logical(source) for source in sources}
        if any(_logical(record.key) not in identities for record in records):
            raise ContractError("Cache records belong to another source field.")

    @staticmethod
    def _checked(prepared, source, language, records):
        if (
            not isinstance(prepared, PreparedContentTranslation)
            or prepared.source != source
            or prepared.target_language != language
        ):
            raise ContractError("Prepared document translation context differs.")

        original = (
            source.field_kind is not ContentFieldKind.TEXT
            or source.source_language == language
            or not source.original_text.strip()
        )
        if original:
            if (
                prepared.display_text != source.original_text
                or prepared.record is not None or prepared.pending is not None
            ):
                raise ContractError("Original or protected document text differs.")
            return prepared

        record = prepared.record
        if (
            not isinstance(record, ContentTranslationRecord)
            or record.key != source.key_for(language)
            or record.translation_status not in (Status.VERIFIED, Status.AUTO_TRANSLATED)
            or prepared.display_text != record.translated_text
        ):
            raise ContractError("Current document translation metadata differs.")

        if prepared.pending is not None:
            if (
                prepared.pending != record
                or record.translation_status is not Status.AUTO_TRANSLATED
                or record.reviewed_by is not None
            ):
                raise ContractError("Provider output cannot claim manual review.")
        elif record not in records:
            raise ContractError("A cached translation must come from supplied records.")
        return prepared

    @staticmethod
    def _translation(prepared):
        record = prepared.record
        return {
            "target_language": prepared.target_language,
            "display_text": prepared.display_text,
            "translation_status": (
                None if record is None else record.translation_status.value),
            "translated_at": (
                None if record is None else record.translated_at.isoformat()),
            "reviewed_by": (
                None if record is None or record.reviewed_by is None
                else str(record.reviewed_by)),
        }

    async def prepare_for_version(
        self, *, language_plan, document_version_id, document_version,
        sources, records=(),
    ):
        self._validate_inputs(
            language_plan, document_version_id, document_version, sources, records)

        memo, pending, fields = {}, {}, []
        for source in sources:
            matching = tuple(
                record for record in records
                if _logical(record.key) == _logical(source))
            translations = []

            for language in language_plan.document_languages:
                memo_key = source, language
                if memo_key not in memo:
                    prepared = await self.translator.prepare(
                        source=source, target_language=language, records=matching)
                    memo[memo_key] = self._checked(
                        prepared, source, language, matching)
                prepared = memo[memo_key]
                if prepared.pending is not None:
                    record = prepared.pending
                    previous = pending.get(record.key)
                    if previous is not None and previous != record:
                        raise ContractError("Pending cache records are ambiguous.")
                    pending[record.key] = record
                translations.append(self._translation(prepared))

            fields.append({
                "entity_type": source.entity_type,
                "entity_id": str(source.entity_id),
                "field_name": source.field_name,
                "field_kind": source.field_kind.value,
                "source_language": source.source_language,
                "source_version": source.source_version,
                "source_text_hash": source.key_for(
                    source.source_language).source_text_hash,
                "original_text": source.original_text,
                "translations": translations,
            })

        # Interface language and organization defaults are not document content.
        payload = {
            "schema_version": 1,
            "tenant_id": str(language_plan.tenant_id),
            "document_version_id": str(document_version_id),
            "document_version": document_version,
            "document_languages": list(language_plan.document_languages),
            "fields": fields,
        }
        return PreparedDocumentTranslationSnapshot(
            snapshot=DocumentTranslationSnapshot(_json(payload)),
            pending=tuple(pending.values()))
