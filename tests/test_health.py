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


async def test_health_exposes_non_secret_llm_config(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/health")).json()
    assert body["commit"]
    assert body["llm_model"]
    assert body["llm_base_host"] == "api.openai.com"
    assert "arxiv" in body["sources"]
