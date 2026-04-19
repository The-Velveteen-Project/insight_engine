from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


async def test_health_is_json(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.headers["content-type"].startswith("application/json")
