"""Tests for the RouterOS REST API client."""

import pytest
import httpx
import json

from src.api.routeros_client import (
    RouterOSClient,
    RouterOSAuthError,
    RouterOSConnectionError,
    RouterOSError,
)


@pytest.fixture
def client():
    return RouterOSClient(
        base_url="https://10.0.0.1/rest",
        username="admin",
        password="test",
        timeout=5.0,
        max_retries=1,
        retry_delay=0.01,
    )


class TestRouterOSClient:
    @pytest.mark.asyncio
    async def test_get_returns_list(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/ip/address",
            json=[
                {".id": "*1", "address": "10.0.0.1/24", "interface": "ether1"},
                {".id": "*2", "address": "10.0.0.2/24", "interface": "ether2"},
            ],
        )
        async with client:
            result = await client.get("ip/address")
        assert len(result) == 2
        assert result[0]["address"] == "10.0.0.1/24"

    @pytest.mark.asyncio
    async def test_get_single_dict_wrapped(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/system/identity",
            json={"name": "router-a"},
        )
        async with client:
            result = await client.get("system/identity")
        assert len(result) == 1
        assert result[0]["name"] == "router-a"

    @pytest.mark.asyncio
    async def test_add_uses_put(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/ip/address",
            method="PUT",
            json={".id": "*3", "address": "10.0.0.3/24"},
        )
        async with client:
            result = await client.add("ip/address", {"address": "10.0.0.3/24", "interface": "ether3"})
        assert result[".id"] == "*3"

    @pytest.mark.asyncio
    async def test_set_uses_patch(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/ip/address/*1",
            method="PATCH",
            json={".id": "*1", "disabled": "true"},
        )
        async with client:
            await client.set("ip/address", "*1", {"disabled": "true"})

    @pytest.mark.asyncio
    async def test_remove_uses_delete(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/ip/address/*1",
            method="DELETE",
            status_code=204,
        )
        async with client:
            await client.remove("ip/address", "*1")

    @pytest.mark.asyncio
    async def test_auth_error(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/system/resource",
            status_code=401,
        )
        async with client:
            with pytest.raises(RouterOSAuthError):
                await client.get("system/resource")

    @pytest.mark.asyncio
    async def test_api_error(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/ip/address",
            status_code=400,
            json={"detail": "invalid input", "error": 400},
        )
        async with client:
            with pytest.raises(RouterOSError) as exc_info:
                await client.get("ip/address")
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_is_reachable_true(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/system/resource",
            json=[{"uptime": "1d", "cpu-load": "5"}],
        )
        async with client:
            assert await client.is_reachable()

    @pytest.mark.asyncio
    async def test_is_reachable_false(self, client, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("refused"))
        async with client:
            assert not await client.is_reachable()

    @pytest.mark.asyncio
    async def test_get_identity(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://10.0.0.1/rest/system/identity",
            json=[{"name": "my-router"}],
        )
        async with client:
            assert await client.get_identity() == "my-router"
