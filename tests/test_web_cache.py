from agentops.app import PREFIX


def test_public_page_and_assets_revalidate_across_releases(client):
    for path in ["/projects/agentops", "/projects/agentops/", "/projects/agentops/assets/app.js"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
    response = client.get("/projects/agentops/assets/app.js")
    validated = client.get(
        "/projects/agentops/assets/app.js", headers={"If-None-Match": response.headers["etag"]}
    )
    assert validated.status_code == 304
    assert validated.headers["cache-control"] == "no-cache"
    assert client.get(PREFIX + "/healthz").headers["cache-control"] == "no-store"
