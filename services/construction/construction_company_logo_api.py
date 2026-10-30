"""Organization logo client using existing Platform Files and profile APIs."""
import asyncio
from dataclasses import dataclass
import http.client
import ssl
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID

from pydantic import SecretStr

from services.construction.construction_api_client import ConstructionApiClientError

MAX_LOGO_BYTES = 10 * 1024 * 1024
SAFE_TYPES = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
}


def problem(status=502, code="invalid_api_response"):
    return ConstructionApiClientError(status_code=status, code=code)


def origin_parts(origin):
    if (
        not isinstance(origin, str) or not origin or len(origin) > 2048
        or not origin.isascii() or any(char.isspace() for char in origin)
    ):
        raise ValueError("Invalid storage origin.")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path not in ("", "/")
    ):
        raise ValueError("Invalid storage origin.")
    return parsed.hostname.lower(), parsed.port or 443


def signed_url(raw, origin, *, upload):
    try:
        expected = origin_parts(origin)
        if (
            not isinstance(raw, str) or not raw or len(raw) > 16384
            or not raw.isascii() or any(char.isspace() for char in raw)
        ):
            raise ValueError("Invalid signed URL.")
        parsed = urlsplit(raw)
        if (
            parsed.scheme != "https"
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment
            or (str(parsed.hostname or "").lower(), parsed.port or 443) != expected
        ):
            raise ValueError("Signed URL origin differs.")
        prefix = (
            "/storage/v1/object/upload/sign/" if upload
            else "/storage/v1/object/sign/"
        )
        decoded = unquote(parsed.path)
        if (
            not parsed.path.startswith(prefix) or not decoded.startswith(prefix)
            or "\\" in decoded or "%" in decoded
            or any(ord(char) < 32 for char in decoded)
            or any(part in ("", ".", "..") for part in decoded[len(prefix):].split("/"))
            or "/" not in decoded[len(prefix):]
        ):
            raise ValueError("Signed URL path differs.")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"token"} or len(query["token"]) != 1 or not query["token"][0]:
            raise ValueError("Signed URL token differs.")
    except (ValueError, TypeError, AttributeError):
        raise problem(code="invalid_storage_url") from None
    return SecretStr(raw)


def validate_image(content, mime, limit):
    if (
        not isinstance(content, bytes) or not 0 < len(content) <= limit
        or mime not in SAFE_TYPES
    ):
        raise problem(422, "invalid_logo_image")
    valid = {
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "image/webp": (
            len(content) >= 12 and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP"
        ),
    }
    if not valid[mime]:
        raise problem(422, "invalid_logo_image")


@dataclass(frozen=True)
class LogoOptions:
    origin: str | None
    max_bytes: int
    extensions: dict
    can_manage: bool


class SignedLogoTransfer:
    """Bounded HTTPS transfer without Bearer headers or URL request logging."""

    async def put(self, *, url, origin, mime, content):
        await asyncio.to_thread(
            self._sync, method="PUT", url=url, origin=origin,
            mime=mime, content=content, size=len(content),
        )

    async def get(self, *, url, origin, mime, size):
        return await asyncio.to_thread(
            self._sync, method="GET", url=url, origin=origin,
            mime=mime, content=None, size=size,
        )

    def _sync(self, *, method, url, origin, mime, content, size):
        if not isinstance(url, SecretStr) or method not in ("GET", "PUT"):
            raise problem(code="invalid_storage_request")
        safe = signed_url(url.get_secret_value(), origin, upload=method == "PUT")
        parsed = urlsplit(safe.get_secret_value())
        connection = None
        try:
            connection = http.client.HTTPSConnection(
                parsed.hostname, parsed.port or 443,
                timeout=20, context=ssl.create_default_context(),
            )
            connection.set_debuglevel(0)
            headers = {"Accept-Encoding": "identity"}
            if method == "PUT":
                headers.update({
                    "Content-Type": mime, "Content-Length": str(size),
                    "Cache-Control": "max-age=0",
                })
            target = parsed.path + "?" + parsed.query
            connection.request(method, target, body=content, headers=headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise problem(503, "storage_transfer_failed")
            if method == "PUT":
                return None
            actual_mime = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            length = response.getheader("Content-Length")
            if (
                actual_mime != mime or encoding != "identity"
                or (length is not None and int(length) != size)
            ):
                raise problem(code="invalid_storage_response")
            chunks, total = [], 0
            while True:
                chunk = response.read(min(65536, size + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > size:
                    raise problem(code="invalid_storage_response")
                chunks.append(chunk)
            if total != size:
                raise problem(code="invalid_storage_response")
            return b"".join(chunks)
        except ConstructionApiClientError:
            raise
        except Exception:
            raise problem(503, "storage_transfer_failed") from None
        finally:
            if connection is not None:
                connection.close()


class CompanyLogoApi:
    def __init__(self, api, *, transfer=None):
        self.api = api
        self.transfer = transfer if transfer is not None else SignedLogoTransfer()

    async def options(self, *, session):
        result = await self.api.company_profile_capabilities(session=session)
        data = result.data
        try:
            maximum = data["image_max_size_bytes"]
            if type(maximum) is not int or maximum <= 0:
                raise ValueError("Image limit differs.")
            if type(data["can_manage_profile"]) is not bool:
                raise ValueError("Profile capability differs.")
            extensions = {}
            if not isinstance(data["image_types"], list):
                raise ValueError("Image types differ.")
            for item in data["image_types"]:
                mime, extension = item["mime_type"], item["extension"]
                if mime in SAFE_TYPES and extension in SAFE_TYPES[mime]:
                    extensions.setdefault(mime, extension)
            if not extensions:
                raise ValueError("Image types are unavailable.")
            origin = data["storage_origin"]
            if origin is not None:
                origin_parts(origin)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return LogoOptions(
            origin=origin, max_bytes=min(maximum, MAX_LOGO_BYTES),
            extensions=extensions, can_manage=data["can_manage_profile"],
        )

    async def upload(self, *, session, content, mime):
        from api.schemas import FileUploadRequestResponse, FileCompleteResponse

        options = await self.options(session=session)
        if not options.can_manage:
            raise problem(403, "permission_required")
        if options.origin is None:
            raise problem(503, "files_unavailable")
        validate_image(content, mime, options.max_bytes)
        if mime not in options.extensions:
            raise problem(422, "invalid_logo_image")
        reserved = await self.api._request(
            "POST", "files/upload-request", session=session, json_body={
                "object_type": "organization", "entity_id": str(session.tenant_id),
                "filename": "organization-logo" + options.extensions[mime],
                "mime_type": mime, "size_bytes": len(content),
            },
        )
        try:
            data = FileUploadRequestResponse.model_validate({
                "data": reserved.data, "meta": reserved.meta,
                "request_id": reserved.request_id,
            }).data
            if (
                data.status != "pending_upload" or data.mime_type != mime
                or type(reserved.data["size_bytes"]) is not int
                or data.size_bytes != len(content)
                or type(reserved.data["expires_in"]) is not int or data.expires_in <= 0
            ):
                raise ValueError("File reservation differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        url = signed_url(data.upload_url, options.origin, upload=True)
        await self.transfer.put(
            url=url, origin=options.origin, mime=mime, content=content,
        )
        completed = await self.api._request(
            "POST", "files/" + str(data.id) + "/complete", session=session,
        )
        try:
            ready = FileCompleteResponse.model_validate({
                "data": completed.data, "meta": completed.meta,
                "request_id": completed.request_id,
            }).data
            if (
                ready.id != data.id or ready.status != "ready"
                or ready.antivirus_status not in ("not_scanned", "clean")
                or ready.mime_type != mime
                or type(completed.data["size_bytes"]) is not int
                or ready.size_bytes != len(content)
                or ready.completed_at.tzinfo is None
                or ready.completed_at.utcoffset() is None
            ):
                raise ValueError("Completed file differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return {"id": str(ready.id), "mime_type": mime, "size_bytes": ready.size_bytes}

    async def download(self, *, session, file_id):
        from api.schemas import FileDownloadResponse

        if not isinstance(file_id, UUID):
            raise ValueError("Logo file UUID is required.")
        options = await self.options(session=session)
        if options.origin is None:
            raise problem(503, "files_unavailable")
        result = await self.api._request(
            "GET", "files/" + str(file_id) + "/download-url", session=session,
        )
        try:
            item = FileDownloadResponse.model_validate({
                "data": result.data, "meta": result.meta, "request_id": result.request_id,
            }).data
            if (
                item.id != file_id or item.mime_type not in options.extensions
                or type(result.data["size_bytes"]) is not int
                or not 0 < item.size_bytes <= options.max_bytes
                or type(result.data["expires_in"]) is not int or item.expires_in <= 0
            ):
                raise ValueError("Download metadata differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        url = signed_url(item.download_url, options.origin, upload=False)
        content = await self.transfer.get(
            url=url, origin=options.origin, mime=item.mime_type, size=item.size_bytes,
        )
        validate_image(content, item.mime_type, options.max_bytes)
        return {
            "id": str(file_id), "content": content, "mime_type": item.mime_type,
            "filename": "organization-logo" + options.extensions[item.mime_type],
        }

    async def save(
        self, *, session, profile_id, expected_row_version,
        logo_file_id, legal_name=None,
    ):
        from api.schemas import CompanyProfileResponse

        options = await self.options(session=session)
        if not options.can_manage:
            raise problem(403, "permission_required")
        if logo_file_id is not None and not isinstance(logo_file_id, UUID):
            raise ValueError("Logo UUID differs.")
        if profile_id is None:
            if (
                expected_row_version is not None or logo_file_id is None
                or not isinstance(legal_name, str) or not legal_name.strip()
                or len(legal_name) > 255
            ):
                raise ValueError("New company profile base differs.")
            method = "POST"
            body = {"legal_name": legal_name, "logo_file_id": str(logo_file_id)}
        else:
            if (
                not isinstance(profile_id, UUID)
                or type(expected_row_version) is not int or expected_row_version <= 0
                or legal_name is not None
            ):
                raise ValueError("Existing company profile base differs.")
            method = "PATCH"
            body = {
                "expected_row_version": expected_row_version,
                "logo_file_id": str(logo_file_id) if logo_file_id is not None else None,
            }
        result = await self.api._request(
            method, "organization/company-profile", session=session, json_body=body,
        )
        try:
            item = CompanyProfileResponse.model_validate({
                "data": result.data, "meta": result.meta, "request_id": result.request_id,
            }).data
            if (
                item.tenant_id != session.tenant_id
                or (profile_id is not None and item.id != profile_id)
                or item.logo_file_id != logo_file_id
                or type(result.data["row_version"]) is not int or item.row_version <= 0
                or not item.legal_name.strip()
                or item.created_at.tzinfo is None or item.created_at.utcoffset() is None
                or item.updated_at.tzinfo is None or item.updated_at.utcoffset() is None
            ):
                raise ValueError("Logo profile receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return item.model_dump(mode="json")
