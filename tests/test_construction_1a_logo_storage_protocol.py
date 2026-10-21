import base64
import json

import httpx
import pytest

from services.portfolio_storage import SupabaseFileStorage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime_type, storage_path, content",
    [
        (
            "image/png",
            "tenant/owner/logo.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                "AAAADUlEQVR4nGOQqLrwHwAEcgJiBGF/zQAAAABJRU5ErkJggg=="
            ),
        ),
        (
            "application/pdf",
            "tenant/owner/document.pdf",
            b"%PDF-1.7\n%%EOF\n",
        ),
    ],
)
async def test_signed_upload_rpc_uses_json_and_binary_upload_keeps_file_mime(
    mime_type, storage_path, content
):
    calls = []
    sign_path = "/storage/v1/object/upload/sign/files/" + storage_path
    binary_path = "/storage/v1/object/files/" + storage_path

    def handler(request):
        assert request.method == "POST"
        assert request.headers["apikey"] == "test-service-key"
        assert request.headers["authorization"] == "Bearer test-service-key"

        if request.url.path == sign_path:
            media_type = request.headers.get("content-type", "").split(";", 1)[0]
            if media_type != "application/json":
                return httpx.Response(
                    415, json={"message": "Signing RPC requires JSON."}
                )
            assert json.loads(request.content) == {}
            calls.append("sign")
            return httpx.Response(
                200,
                json={
                    "url": "/object/upload/sign/files/" + storage_path
                    + "?token=test-upload-token"
                },
            )

        assert request.url.path == binary_path
        assert request.headers["content-type"] == mime_type
        assert request.headers["x-upsert"] == "false"
        assert request.content == content
        calls.append("binary")
        return httpx.Response(200, json={"Key": "files/" + storage_path})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        storage = SupabaseFileStorage(
            base_url="https://storage.fixture.test",
            service_role_key="test-service-key",
            bucket="files",
            client=client,
        )
        signed_url = await storage.create_signed_upload_url(
            storage_path=storage_path, mime_type=mime_type
        )
        assert signed_url == (
            "https://storage.fixture.test" + sign_path + "?token=test-upload-token"
        )
        await storage.upload(
            storage_path=storage_path, content=content, mime_type=mime_type
        )

    assert calls == ["sign", "binary"]
