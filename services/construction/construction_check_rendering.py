"""CON-020 check messages via the existing versioned Platform label localizer.

Use a dedicated clean Platform session: this service owns only the static-label
cache transaction. Domain authorization and assessment remain with the caller.
Technical codes, exact parameters and the original blocking decision are preserved.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.platform_catalog_localization import (
    CatalogLabel, CatalogLanguageUnavailable, CatalogTranslationUnavailable,
    PreparedCatalogLabels, build_platform_catalog_localizer, label_text)
from services.construction.construction_checks import (
    CHECK_RULES, ConstructionCheckSeverity as Severity, ConstructionCheckReport as Report)

LEVEL_KEYS = MappingProxyType({
    Severity.ERROR: "construction.checks.level.error",
    Severity.WARNING: "construction.checks.level.warning",
    Severity.INFO: "construction.checks.level.info",
})


class ConstructionCheckRenderingDenied(Exception):
    pass


class ConstructionCheckRenderingError(Exception):
    pass


class ConstructionCheckRenderingLanguageError(ConstructionCheckRenderingError):
    code = "LANGUAGE_CONFIGURATION_REQUIRED"


class ConstructionCheckRenderingLanguageChanged(ConstructionCheckRenderingLanguageError):
    code = "LANGUAGE_CONTEXT_CHANGED"


class ConstructionCheckTranslationUnavailable(ConstructionCheckRenderingError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate check label key.")
        result[key] = value
    return result


def check_label_source():
    path = Path(__file__).resolve().parents[2] / "locales" / "construction_checks.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_object)
    expected = {rule.message_key for rule in CHECK_RULES.values()} | set(LEVEL_KEYS.values())
    if (not isinstance(data, dict) or set(data) != {"version", "keys", "labels"}
            or type(data["version"]) is not int or data["version"] <= 0
            or not isinstance(data["keys"], list) or len(data["keys"]) != len(expected)
            or any(not isinstance(key, str) for key in data["keys"]) or set(data["keys"]) != expected
            or not isinstance(data["labels"], dict) or "en" not in data["labels"]):
        raise ValueError("Check label source is invalid.")
    supplied = {}
    for language, values in data["labels"].items():
        if (not isinstance(language, str) or not 1 <= len(language) <= 10 or language != language.strip()
                or not isinstance(values, list) or len(values) != len(expected)):
            raise ValueError("Check label language is invalid.")
        supplied[language] = dict(zip(data["keys"], (label_text(value) for value in values)))
    return data["version"], supplied["en"], supplied


@dataclass(frozen=True)
class LocalizedConstructionChecks:
    report: Report
    language_code: str
    source_version: int
    labels: tuple[CatalogLabel, ...]

    def to_payload(self):
        by_key = {label.key: label for label in self.labels}
        findings = []
        for finding in self.report.findings:
            message = by_key[finding.message_key]
            level = by_key[LEVEL_KEYS[finding.severity]]
            findings.append(finding.to_payload() | {
                "message": message.text, "severity_label": level.text,
                "message_status": message.status, "severity_label_status": level.status})
        return {
            "language_code": self.language_code, "label_source_version": self.source_version,
            "is_blocked": self.report.is_blocked, "requires_attention": self.report.requires_attention,
            "findings": findings,
        }


class ConstructionCheckRenderingService:
    def __init__(self, *, session, identity, localizer=None):
        if (session is None or not hasattr(session, "no_autoflush")
                or getattr(identity, "session", None) is not session
                or not callable(getattr(identity, "require_actor", None))
                or any(not callable(getattr(session, name, None)) for name in
                       ("in_transaction", "get_nested_transaction", "expire_all", "commit", "rollback"))):
            raise ValueError("Check rendering requires a dedicated Platform session.")
        self.session, self.identity = session, identity
        self.localizer = localizer if localizer is not None else build_platform_catalog_localizer(session)
        if (getattr(self.localizer, "session", None) is not session
                or any(not callable(getattr(self.localizer, name, None)) for name in ("prepare", "persist"))
                or not callable(getattr(getattr(self.localizer, "repository", None), "is_active_language", None))):
            raise ValueError("Check label localization must share the Platform session.")

    @staticmethod
    def _actor(actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != "active"
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise ConstructionCheckRenderingDenied("Check messages are unavailable.")

    async def _current(self, actor):
        self._actor(actor)
        self.session.expire_all()
        current = await self.identity.require_actor(user_id=actor.user_id, tenant_id=actor.tenant_id)
        self._actor(current)
        if current.user_id != actor.user_id or current.tenant_id != actor.tenant_id:
            raise ConstructionCheckRenderingDenied("Check messages are unavailable.")
        if (not isinstance(current.language_code, str) or not 1 <= len(current.language_code) <= 10
                or current.language_code != current.language_code.strip()):
            raise ConstructionCheckRenderingLanguageError("Profile language is unavailable.")
        return current

    async def _unchanged(self, actor, language):
        if await self.localizer.repository.is_active_language(language) is not True:
            raise ConstructionCheckRenderingLanguageError("Profile language is unavailable.")
        current = await self._current(actor)
        if current.language_code != language:
            raise ConstructionCheckRenderingLanguageChanged("Profile language changed.")

    @staticmethod
    def _prepared(prepared, *, language, version, wanted):
        if (not isinstance(prepared, PreparedCatalogLabels) or prepared.language_code != language
                or type(prepared.source_version) is not int or prepared.source_version != version
                or not isinstance(prepared.labels, tuple) or not isinstance(prepared.pending, tuple)
                or len(prepared.labels) != len(wanted)):
            raise ValueError("Check label response differs.")
        labels = {}
        technical = {code.value for code in CHECK_RULES} | {level.value for level in Severity}
        for label in prepared.labels:
            if (not isinstance(label, CatalogLabel) or label.key not in wanted or label.key in labels
                    or label.status not in {"provided", "cached", "translated"}
                    or label_text(label.text) == label.key or label.text in technical):
                raise ValueError("Check label projection is invalid.")
            labels[label.key] = label
        if set(labels) != set(wanted):
            raise ValueError("Check labels are incomplete.")
        return tuple(labels[key] for key in wanted)

    async def render_for_actor(self, *, actor, report):
        self._actor(actor)
        if (not isinstance(report, Report) or report.context.user_id != actor.user_id
                or report.context.tenant_id != actor.tenant_id):
            raise ConstructionCheckRenderingDenied("Check report context differs.")
        if (self.session.in_transaction() or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionCheckRenderingError("A dedicated clean Platform session is required.")
        try:
            with self.session.no_autoflush:
                current = await self._current(actor)
                language = current.language_code
                version, source, supplied = check_label_source()
                wanted = {}
                for finding in report.findings:
                    for key in (finding.message_key, LEVEL_KEYS[finding.severity]):
                        wanted[key] = source[key]
                if not wanted:
                    await self._unchanged(actor, language)
                    result = LocalizedConstructionChecks(report, language, version, ())
                    await self.session.rollback()
                    return result
                prepared = await self.localizer.prepare(
                    namespace="construction.checks", source_version=version, source_labels=wanted,
                    supplied_labels={key: value for key, value in supplied.get(language, {}).items() if key in wanted},
                    language_code=language)
                labels = self._prepared(prepared, language=language, version=version, wanted=wanted)
                await self._unchanged(actor, language)
                result = LocalizedConstructionChecks(report, language, version, labels)
                result.to_payload()
                if prepared.pending:
                    await self.localizer.persist(prepared)
                    await self._unchanged(actor, language)
                    await self.session.commit()
                else:
                    await self.session.rollback()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionCheckRenderingError("Check message rollback failed.") from None
            if not isinstance(error, Exception):
                raise
            if isinstance(error, ApiIdentityAccessError):
                raise ConstructionCheckRenderingDenied("Check messages are unavailable.") from None
            if isinstance(error, CatalogLanguageUnavailable):
                raise ConstructionCheckRenderingLanguageError("Profile language is unavailable.") from None
            if isinstance(error, CatalogTranslationUnavailable):
                raise ConstructionCheckTranslationUnavailable("Check translation is unavailable.") from None
            if isinstance(error, (ConstructionCheckRenderingDenied, ConstructionCheckRenderingError)):
                raise
            raise ConstructionCheckRenderingError("Check message rendering failed.") from None


def build_construction_check_rendering_service(*, platform_session):
    from services.api_identity import ApiIdentityService
    return ConstructionCheckRenderingService(
        session=platform_session, identity=ApiIdentityService(platform_session))
