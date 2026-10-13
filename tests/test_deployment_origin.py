import pytest
from fastapi.testclient import TestClient

from agentops.app import create_app
from agentops.settings import Settings


def test_custom_https_domain_accepts_matching_origin_and_sets_secure_cookie(tmp_path):
    settings = Settings(
        db_path=tmp_path / "domain.db", public_origin="https://router.example.com", secure_cookies=True
    )
    with TestClient(create_app(settings), base_url="https://router.example.com") as client:
        response = client.post(
            "/api/projects/agentops/session", headers={"Origin": "https://router.example.com"}
        )
        assert response.status_code == 200
        assert "; Secure" in response.headers["set-cookie"]
        assert (
            client.post(
                "/api/projects/agentops/session", headers={"Origin": "https://attacker.example"}
            ).status_code
            == 403
        )
        assert client.get("/healthz", headers={"Host": "attacker.example"}).status_code == 400


@pytest.mark.parametrize(
    "origin",
    [
        "http://router.example.com",
        "https://user:password@router.example.com",
        "https://router.example.com/path",
        "https://router.example.com?q=1",
    ],
)
def test_unsafe_public_origin_configuration_is_rejected(origin):
    with pytest.raises(ValueError):
        Settings(public_origin=origin, secure_cookies=True)


def test_https_origin_requires_secure_cookies():
    with pytest.raises(ValueError):
        Settings(public_origin="https://router.example.com", secure_cookies=False)
