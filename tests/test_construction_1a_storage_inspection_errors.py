import httpx
import pytest

from services.portfolio_storage import PortfolioStorageError, SupabaseFileStorage


BASE = "https://storage.fixture.test"
PATH = "tenant/owner/logo.png"
HEAD_PATH = "/storage/v1/object/files/" + PATH
INFO_PATH = "/storage/v1/object/info/authenticated/files/" + PATH


def storage_for(client):
    return SupabaseFileStorage(
        base_url=BASE, service_role_key="test-service-key",
        bucket="files", timeout_seconds=7, client=client,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("info_status, payload", [
    (400, {"code": "NoSuchKey", "statusCode": "404",
           "error": "not_found", "message": "Object not found"}),
    (400, {"code": "NoSuchKey", "statusCode": 404}),
    (404, {"code": "NoSuchKey", "message": "Object not found"}),
    (400, {"code": "NoSuchKey", "message": "Object not found"}),
])
async def test_missing_object_is_confirmed_by_structured_storage_code(info_status, payload):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["apikey"] == "test-service-key"
        assert request.headers["authorization"] == "Bearer test-service-key"
        if request.method == "HEAD":
            assert request.url.path == HEAD_PATH
            return httpx.Response(400)
        assert request.method == "GET" and request.url.path == INFO_PATH
        return httpx.Response(info_status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await storage_for(client).inspect_object(storage_path=PATH)

    assert result.exists is False
    assert result.size_bytes is None and result.mime_type is None
    assert result.provider_metadata == {}
    assert calls == [("HEAD", HEAD_PATH), ("GET", INFO_PATH)]


@pytest.mark.asyncio
@pytest.mark.parametrize("info_status, payload", [
    (400, {"code": "AccessDenied", "statusCode": "404"}),
    (400, {"code": "NoSuchBucket", "statusCode": "404"}),
    (400, {"code": "InvalidJWT"}),
    (400, {"statusCode": "404", "error": "not_found", "message": "Object not found"}),
    (400, {}),
    (400, []),
    (400, "not-json"),
    (400, {"code": "NoSuchKey", "statusCode": "500"}),
    (401, {"code": "NoSuchKey", "statusCode": "404"}),
    (403, {"code": "NoSuchKey", "statusCode": "404"}),
    (500, {"code": "NoSuchKey", "statusCode": "404"}),
    (200, {"code": "NoSuchKey", "statusCode": "404"}),
])
async def test_other_errors_are_not_reported_as_missing_objects(info_status, payload):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "HEAD":
            return httpx.Response(400)
        assert request.method == "GET" and request.url.path == INFO_PATH
        if isinstance(payload, str):
            return httpx.Response(info_status, content=payload)
        return httpx.Response(info_status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PortfolioStorageError) as caught:
            await storage_for(client).inspect_object(storage_path=PATH)

    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    assert calls == [("HEAD", HEAD_PATH), ("GET", INFO_PATH)]


@pytest.mark.asyncio
@pytest.mark.parametrize("head_status", [401, 403, 409, 415, 429, 500])
async def test_non_400_head_errors_do_not_trigger_a_fallback(head_status):
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "HEAD"
        return httpx.Response(head_status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PortfolioStorageError):
            await storage_for(client).inspect_object(storage_path=PATH)
    assert calls == ["HEAD"]


@pytest.mark.asyncio
async def test_head_404_remains_a_missing_object_without_an_extra_request():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await storage_for(client).inspect_object(storage_path=PATH)
    assert result.exists is False and calls == ["HEAD"]


@pytest.mark.asyncio
async def test_existing_object_keeps_head_metadata_without_an_extra_request():
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "HEAD"
        return httpx.Response(200, headers={
            "content-length": "70", "content-type": "image/png; charset=binary",
            "etag": '"test-etag"', "last-modified": "Mon, 05 Oct 2026 09:00:00 GMT",
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await storage_for(client).inspect_object(storage_path=PATH)
    assert result.exists is True
    assert result.size_bytes == 70 and result.mime_type == "image/png"
    assert result.provider_metadata == {
        "etag": '"test-etag"', "last_modified": "Mon, 05 Oct 2026 09:00:00 GMT",
    }
    assert calls == ["HEAD"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_method", ["HEAD", "GET"])
async def test_transport_errors_are_not_reported_as_missing_objects(failing_method):
    def handler(request):
        if request.method == failing_method:
            raise httpx.ReadTimeout("Test timeout", request=request)
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PortfolioStorageError) as caught:
            await storage_for(client).inspect_object(storage_path=PATH)
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
