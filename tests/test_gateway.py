import httpx

from sleepy.web import Metrics, filtered_headers, proxy_client


async def test_shared_pool_never_retains_an_admin_cookie():
    requests = []

    async def upstream(request):
        requests.append(request)
        if request.url.path == "/login":
            return httpx.Response(
                200,
                headers=[
                    ("Set-Cookie", "session=admin; Path=/"),
                    ("Set-Cookie", "other=value; Path=/"),
                ],
            )
        return httpx.Response(200)

    async with proxy_client(transport=httpx.MockTransport(upstream)) as client:
        response = await client.get("http://backend/login")
        assert len(response.headers.get_list("set-cookie")) == 2
        await client.get("http://backend/status")
        await client.get("http://backend/status", headers={"Cookie": "session=visitor"})
        assert requests[1].headers.get("cookie") is None
        assert requests[2].headers.get("cookie") == "session=visitor"
        assert not list(client.cookies.jar)


def test_hop_headers_and_spoofed_identity_are_removed():
    raw = [
        (b"Connection", b"custom, keep-alive"),
        (b"custom", b"secret"),
        (b"X-Forwarded-For", b"evil"),
        (b"Forwarded", b"evil"),
        (b"X-Sleepy-Internal", b"evil"),
        (b"Cookie", b"actual=visitor"),
        (b"Authorization", b"Bearer actual"),
    ]
    assert filtered_headers(raw, request=True) == [
        (b"Cookie", b"actual=visitor"),
        (b"Authorization", b"Bearer actual"),
    ]


async def test_metrics_retry_is_same_batch_and_ignores_internal_requests():
    calls = []

    async def upstream(request):
        calls.append(request.content)
        return httpx.Response(503 if len(calls) == 1 else 204)

    async with proxy_client(transport=httpx.MockTransport(upstream)) as client:
        metrics = Metrics("http://backend", "internal", client)
        metrics.record({"path": "/", "method": "GET"}, 200, 120)
        metrics.record({"path": "/assets/a.js", "method": "GET"}, 200, 120)
        metrics.record({"path": "/_internal/metrics", "method": "POST"}, 204, 120)
        await metrics.flush()
        assert len(metrics.pending) == 1
        await metrics.flush()
        assert not metrics.pending
        assert calls[0] == calls[1]
