"""Shared versioned catalog localization using the existing Platform provider/cache."""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import math

from services.translation import LibreTranslateProvider


class CatalogLanguageUnavailable(ValueError):
    pass


class CatalogTranslationUnavailable(Exception):
    pass


@dataclass(frozen=True)
class CatalogLabel:
    key: str
    text: str
    status: str


@dataclass(frozen=True)
class PreparedCatalogLabels:
    language_code: str
    source_version: int
    labels: tuple[CatalogLabel, ...]
    pending: tuple[dict, ...]


def label_text(value):
    if (
        not isinstance(value, str) or not value.strip()
        or value != value.strip() or len(value) > 120
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Catalog label is invalid.")
    return value


def catalog_source_hash(namespace, key, version, text):
    data = dict(namespace=namespace, key=key, version=version, text=text)
    return hashlib.sha256(json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


class PlatformCatalogLocalizer:
    def __init__(self, *, repository, provider=None):
        self.repository = repository
        self.session = repository.session
        self.provider = provider

    async def prepare(self, *, namespace, source_version, source_labels, supplied_labels, language_code):
        if (
            not isinstance(language_code, str) or not 1 <= len(language_code) <= 10
            or language_code != language_code.strip()
            or not await self.repository.is_active_language(language_code)
        ):
            raise CatalogLanguageUnavailable("An active profile language is required.")
        if (
            type(source_version) is not int or source_version <= 0
            or not isinstance(namespace, str) or not namespace.strip()
            or len(namespace) > 128
            or any(not isinstance(key, str) or not key.strip() or len(key) > 128 for key in source_labels)
            or not source_labels or set(supplied_labels) - set(source_labels)
        ):
            raise ValueError("Catalog source is invalid.")
        source = {key: label_text(value) for key, value in source_labels.items()}
        labels = {key: CatalogLabel(key, label_text(value), "provided")
                  for key, value in supplied_labels.items()}
        missing = [key for key in source if key not in labels]
        if not missing:
            return PreparedCatalogLabels(language_code, source_version,
                tuple(labels[key] for key in source), ())

        provider = "libretranslate" if self.provider is None else getattr(self.provider, "provider_name", None)
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 100:
            raise ValueError("Catalog translation provider is invalid.")
        hashes = {key: catalog_source_hash(namespace, key, source_version, source[key])
                  for key in missing}
        cached = await self.repository.read_labels(
            hashes=tuple(hashes.values()), source_language="en",
            target_language=language_code, provider=provider,
        )
        for key in missing:
            value = cached.get(hashes[key])
            if value is not None:
                try:
                    labels[key] = CatalogLabel(key, label_text(value), "cached")
                except ValueError:
                    pass
        missing = [key for key in missing if key not in labels]
        if not missing:
            return PreparedCatalogLabels(language_code, source_version,
                tuple(labels[key] for key in source), ())
        active_provider = self.provider if self.provider is not None else LibreTranslateProvider()
        semaphore = asyncio.Semaphore(4)

        async def translate(key):
            async with semaphore:
                value = await active_provider.translate(
                    text=source[key], source_language="en", target_language=language_code,
                )
                return key, label_text(value)

        timeout = float(getattr(active_provider, "timeout_seconds", 15))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Catalog translation timeout is invalid.")
        tasks = [asyncio.create_task(translate(key)) for key in missing]
        try:
            translated = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
        except BaseException as error:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not isinstance(error, Exception):
                raise
            raise CatalogTranslationUnavailable("Catalog translation is unavailable.") from None

        pending = []
        for key, text in translated:
            labels[key] = CatalogLabel(key, text, "translated")
            pending.append(dict(
                source_text_hash=hashes[key], source_language="en",
                target_language=language_code, translated_text=text, provider=provider,
            ))
        return PreparedCatalogLabels(language_code, source_version,
            tuple(labels[key] for key in source), tuple(pending))

    async def persist(self, prepared):
        await self.repository.save_labels(prepared.pending)


def build_platform_catalog_localizer(session):
    from database.repositories.platform_catalog_translations import PlatformCatalogTranslationRepository
    return PlatformCatalogLocalizer(repository=PlatformCatalogTranslationRepository(session))
