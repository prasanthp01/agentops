import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import PrivateConfig


@dataclass
class Settings:
    public_origin: str = field(default_factory=lambda: os.getenv("AGENTOPS_PUBLIC_ORIGIN", ""))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("AGENTOPS_DB", "var/agentops.db")))
    secure_cookies: bool = field(
        default_factory=lambda: os.getenv("AGENTOPS_SECURE_COOKIES", "false") == "true"
    )
    workers: int = 2
    lease_seconds: int = 30
    run_ttl_seconds: int = 3600
    session_ttl_seconds: int = 7200
    max_active: int = 64
    max_per_owner: int = 4
    runs_per_minute: int = 30
    global_runs_per_minute: int = 300
    max_retained_runs: int = 2000
    max_run_seconds: int = 60
    private: PrivateConfig = field(default_factory=PrivateConfig)

    def __post_init__(self):
        if not self.public_origin:
            return
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("AGENTOPS_PUBLIC_ORIGIN must be an HTTP(S) origin without credentials or a path")
        if origin.scheme == "http" and origin.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            raise ValueError("Public deployments require an HTTPS origin")
        if origin.scheme == "https" and not self.secure_cookies:
            raise ValueError("HTTPS origins require AGENTOPS_SECURE_COOKIES=true")
        self.public_origin = self.public_origin.rstrip("/")

    @property
    def allowed_hosts(self):
        hosts = ["127.0.0.1", "localhost", "testserver"]
        if self.public_origin:
            hosts.append(urlsplit(self.public_origin).hostname)
        return hosts

    @classmethod
    def from_env(cls):
        settings = cls()
        config = os.getenv("AGENTOPS_PRIVATE_CONFIG")
        if config:
            settings.private = PrivateConfig.model_validate_json(Path(config).read_text())
        return settings
