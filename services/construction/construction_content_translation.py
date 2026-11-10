"""Version-bound content translation preparation, following rev.12 §6.6.

Internal component only. The caller must authorize the entity, read its canonical
source/version, classify the field and obtain the target language from Platform.
No database writes, commits, public API, identity lookup or access grants here.
The provider uses the existing Platform translate(text, source_language,
target_language) interface. Prepared automatic records are not persisted here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import re
from uuid import UUID


MAX_TEXT_BYTES = 1024 * 1024


class ContentTranslationContractError(ValueError):
    pass


class ContentTranslationUnavailable(Exception):
    pass


class ContentTranslationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    AUTO_TRANSLATED = "AUTO_TRANSLATED"
    STALE = "STALE"


class ContentFieldKind(str, Enum):
    TEXT = "TEXT"
    PERSON_NAME = "PERSON_NAME"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    LEGAL_DETAILS = "LEGAL_DETAILS"
    NUMBER = "NUMBER"
    FORMULA = "FORMULA"
    UNIT_CODE = "UNIT_CODE"
    SYSTEM_CODE = "SYSTEM_CODE"
    NORMATIVE_REFERENCE = "NORMATIVE_REFERENCE"


def _language(value):
    if (
        not isinstance(value, str) or len(value) > 10
        or re.fullmatch(r"[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,6})?", value) is None
    ):
        raise ContentTranslationContractError("An exact language code is required.")
    return value


def _machine(value, *, maximum):
    if (
        not isinstance(value, str) or not 1 <= len(value) <= maximum
        or re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*", value) is None
    ):
        raise ContentTranslationContractError("A canonical field identity is required.")


def _text(value):
    if not isinstance(value, str) or "\x00" in value:
        raise ContentTranslationContractError("Source text must be valid text.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ContentTranslationContractError("Source text must be valid UTF-8.") from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise ContentTranslationContractError("Translation text exceeds its bound.")
    # Preserve whitespace, punctuation and Unicode exactly.
    return value


def _identity(entity_type, entity_id, field_name, source_language, source_version):
    _machine(entity_type, maximum=128)
    _machine(field_name, maximum=255)
    if not isinstance(entity_id, UUID):
        raise ContentTranslationContractError("A canonical entity UUID is required.")
    _language(source_language)
    if type(source_version) is not int or not 1 <= source_version <= 2**63 - 1:
        raise ContentTranslationContractError("A positive source version is required.")


@dataclass(frozen=True)
class ContentTranslationKey:
    entity_type: str
    entity_id: UUID
    field_name: str
    source_language: str
    target_language: str
    source_version: int
    source_text_hash: str

    def __post_init__(self):
        _identity(
            self.entity_type, self.entity_id, self.field_name,
            self.source_language, self.source_version)
        _language(self.target_language)
        if (
            not isinstance(self.source_text_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_text_hash) is None
        ):
            raise ContentTranslationContractError("An exact source hash is required.")


@dataclass(frozen=True)
class ContentTranslationSource:
    entity_type: str
    entity_id: UUID
    field_name: str
    source_language: str
    source_version: int
    original_text: str
    field_kind: ContentFieldKind

    def __post_init__(self):
        _identity(
            self.entity_type, self.entity_id, self.field_name,
            self.source_language, self.source_version)
        _text(self.original_text)
        if not isinstance(self.field_kind, ContentFieldKind):
            raise ContentTranslationContractError(
                "The canonical reader must classify the field.")

    def key_for(self, target_language):
        return ContentTranslationKey(
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            field_name=self.field_name,
            source_language=self.source_language,
            target_language=_language(target_language),
            source_version=self.source_version,
            source_text_hash=sha256(
                self.original_text.encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class ContentTranslationRecord:
    key: ContentTranslationKey
    translated_text: str
    translation_status: ContentTranslationStatus
    translated_at: datetime
    reviewed_by: UUID | None = None

    def __post_init__(self):
        if (
            not isinstance(self.key, ContentTranslationKey)
            or not isinstance(self.translation_status, ContentTranslationStatus)
            or self.reviewed_by is not None and not isinstance(self.reviewed_by, UUID)
        ):
            raise ContentTranslationContractError("Translation metadata is invalid.")
        if not _text(self.translated_text).strip():
            raise ContentTranslationContractError("Translation text must not be empty.")
        instant = self.translated_at
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None or instant.utcoffset() is None
        ):
            raise ContentTranslationContractError("An aware translation timestamp is required.")
        object.__setattr__(
            self, "translated_at", instant.astimezone(timezone.utc))


@dataclass(frozen=True)
class PreparedContentTranslation:
    source: ContentTranslationSource
    target_language: str
    display_text: str
    record: ContentTranslationRecord | None
    pending: ContentTranslationRecord | None

    @property
    def original_text(self):
        return self.source.original_text

    @property
    def used_translation(self):
        return self.record is not None

    @property
    def translation_status(self):
        return None if self.record is None else self.record.translation_status


def select_content_translation(*, source, target_language, records):
    if (
        not isinstance(source, ContentTranslationSource)
        or not isinstance(records, tuple)
        or any(not isinstance(item, ContentTranslationRecord) for item in records)
    ):
        raise ContentTranslationContractError("Typed canonical translation records are required.")
    wanted = source.key_for(target_language)
    candidates = {
        ContentTranslationStatus.VERIFIED: [],
        ContentTranslationStatus.AUTO_TRANSLATED: [],
    }
    logical_identity = (wanted.entity_type, wanted.entity_id, wanted.field_name)
    for record in records:
        key = record.key
        if (key.entity_type, key.entity_id, key.field_name) != logical_identity:
            raise ContentTranslationContractError(
                "Translation records belong to another field.")
        if key == wanted and record.translation_status in candidates:
            candidates[record.translation_status].append(record)

    # A conflicting current record is a storage/integration error, not a reason
    # to choose an arbitrary translation.
    if any(len(items) > 1 for items in candidates.values()):
        raise ContentTranslationContractError("Current translation records are ambiguous.")
    for status in (
        ContentTranslationStatus.VERIFIED,
        ContentTranslationStatus.AUTO_TRANSLATED,
    ):
        if candidates[status]:
            return candidates[status][0]
    return None


class ConstructionContentTranslator:
    def __init__(self, *, provider=None, now_provider=None):
        if provider is not None and not callable(getattr(provider, "translate", None)):
            raise ValueError("A Platform-compatible translation provider is required.")
        if now_provider is not None and not callable(now_provider):
            raise ValueError("Translation clock must be callable.")
        self.provider = provider
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def prepare(self, *, source, target_language, records=()):
        if not isinstance(source, ContentTranslationSource):
            raise ContentTranslationContractError("A canonical content source is required.")
        _language(target_language)
        if (
            not isinstance(records, tuple)
            or any(not isinstance(item, ContentTranslationRecord) for item in records)
        ):
            raise ContentTranslationContractError("Typed translation records are required.")

        # Field classification comes from the trusted domain reader, never from
        # an HTTP caller. Protected data is returned byte-for-byte as supplied.
        if (
            source.field_kind is not ContentFieldKind.TEXT
            or source.source_language == target_language
            or not source.original_text.strip()
        ):
            return PreparedContentTranslation(
                source, target_language, source.original_text, None, None)

        cached = select_content_translation(
            source=source, target_language=target_language, records=records)
        if cached is not None:
            return PreparedContentTranslation(
                source, target_language, cached.translated_text, cached, None)
        if self.provider is None:
            raise ContentTranslationUnavailable(
                "Content translation provider is unavailable.")

        try:
            translated = await self.provider.translate(
                text=source.original_text,
                source_language=source.source_language,
                target_language=target_language)
            if not _text(translated).strip():
                raise ValueError()
        except Exception:
            raise ContentTranslationUnavailable(
                "Content translation is unavailable.") from None

        record = ContentTranslationRecord(
            key=source.key_for(target_language),
            translated_text=translated,
            translation_status=ContentTranslationStatus.AUTO_TRANSLATED,
            translated_at=self.now_provider(),
            reviewed_by=None)
        return PreparedContentTranslation(
            source, target_language, translated, record, record)
