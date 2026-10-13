"""Explicit, checksum-verified import of the frozen local Contracts release."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "b55d1a9dc5d2bab23817b984b9801ccff7304f04"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    source = parser.parse_args().source.resolve()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if sha != RELEASE or subprocess.check_output(["git", "status", "--porcelain"], cwd=source):
        raise SystemExit("Expected clean, pinned Contracts v1.0.0 release")
    manifest = json.loads((source / "contracts-manifest.json").read_text())
    for group in ("files", "test_reports"):
        for name, expected in manifest[group].items():
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
                raise SystemExit(f"Manifest mismatch: {name}")
    target = ROOT / "agentops/vendor/ecosystem_contracts"
    target.mkdir(parents=True, exist_ok=True)
    (target.parent / "__init__.py").touch()
    files = {}

    def copy(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        files[str(dst.relative_to(ROOT))] = hashlib.sha256(dst.read_bytes()).hexdigest()

    for name in ("__init__.py", "validation.py"):
        copy(source / "src/ecosystem_contracts" / name, target / name)
    copy(source / "openapi/ecosystem-v1.json", target / "openapi.json")
    for src in sorted((source / "schemas/v1").glob("*.json")):
        copy(src, target / "schemas/v1" / src.name)
    for group in ("valid", "invalid"):
        for src in sorted((source / "examples" / group).glob("*.json")):
            copy(src, ROOT / "tests/fixtures/contracts" / group / src.name)
    for name in ("contracts-manifest.json", "docs/PROTOCOL.md", "openapi/ecosystem-v1.json"):
        copy(source / name, ROOT / "contracts/canonical" / Path(name).name)
    for name in ("LICENSE", "LICENSE.md", "NOTICE"):
        if (source / name).exists():
            copy(source / name, target / name)
    lock = {
        "contract_version": "1.0.0",
        "wire_version": "1.0",
        "release_commit": RELEASE,
        "tested_source_commit": manifest["release_source_commit"],
        "verified_manifest_files": len(manifest["files"]),
        "verified_report_files": len(manifest["test_reports"]),
        "files": files,
    }
    (ROOT / "contracts.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    print(f"Verified release and vendored {len(files)} files")


if __name__ == "__main__":
    main()
