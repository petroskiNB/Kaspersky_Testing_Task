"""Prepare one pinned Qwen snapshot, then exit; no background polling."""

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys


MARKER = ".model-ready.json"
REQUIRED = ("config.json", "tokenizer.json", "tokenizer_config.json")


def emit(event, **metadata):
    # Only constant event names; never print tokens or upstream exceptions.
    print(json.dumps({"event": event, **metadata}), flush=True)


def safe_error_metadata(error):
    """Expose error categories without URLs, credentials or exception text."""
    result = {"error_type": type(error).__name__}
    chain = []
    visited = set()
    current = error
    while current is not None and id(current) not in visited and len(chain) < 6:
        visited.add(id(current))
        chain.append(type(current).__name__)
        number = getattr(current, "errno", None)
        if isinstance(number, int):
            result["errno"] = number
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            result["http_status"] = status
        current = current.__cause__ or current.__context__
    result["error_chain"] = chain
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_snapshot(directory, manifest):
    files = manifest.get("files", {})
    if not isinstance(files, dict) or not all(name in files for name in REQUIRED):
        return False
    if not any(name.endswith(".safetensors") for name in files):
        return False
    for name, attributes in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            return False
        path = directory / relative
        if not path.is_file() or path.is_symlink():
            return False
        if path.stat().st_size != attributes["size"] or sha256_file(path) != attributes["sha256"]:
            return False
    return True


def prepare_model(model_id, revision, directory, download=None):
    if not model_id or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("MODEL_REVISION must be a full commit SHA")
    revision = revision.lower()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Also prevents concurrent initializers from modifying the same snapshot.
    from filelock import FileLock

    with FileLock(str(directory.parent / (directory.name + ".download.lock")), timeout=1800):
        marker = directory / MARKER
        if marker.exists():
            try:
                manifest = json.loads(marker.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                manifest = {}
            if manifest.get("model_id") != model_id or manifest.get("revision") != revision:
                raise ValueError("Existing snapshot identity differs; use a separate model directory")
            if valid_snapshot(directory, manifest):
                emit("model_cache_hit")
                return
            # HF local_dir metadata can consider a corrupted local file current.
            # Remove only the damaged snapshot files so snapshot_download fetches
            # them again rather than accepting stale local cache metadata.
            for name, attributes in manifest.get("files", {}).items():
                if Path(name).name != name:
                    raise ValueError("Unsafe snapshot manifest")
                path = directory / name
                if path.is_file() and (
                    path.is_symlink() or path.stat().st_size != attributes["size"]
                    or sha256_file(path) != attributes["sha256"]
                ):
                    path.unlink()
            marker.unlink()

        emit("model_download_started")
        if download is None:
            # Import only on a cache miss. Cache hits never initialize HF clients.
            logging.disable(logging.CRITICAL)
            from huggingface_hub import snapshot_download
            from huggingface_hub.utils import disable_progress_bars

            disable_progress_bars()
            download = snapshot_download
        download(
            repo_id=model_id,
            revision=revision,
            local_dir=str(directory),
            token=os.environ.get("HF_TOKEN") or False,
            allow_patterns=["*.json", "*.safetensors", "*.txt", "LICENSE"],
            max_workers=2,
        )

        for name in REQUIRED:
            json.loads((directory / name).read_text(encoding="utf-8"))
        weights = list(directory.glob("*.safetensors"))
        if not weights or any(path.stat().st_size == 0 for path in weights):
            raise ValueError("Missing or empty weights")
        index = directory / "model.safetensors.index.json"
        if index.exists():
            for name in json.loads(index.read_text(encoding="utf-8"))["weight_map"].values():
                if Path(name).name != name or not (directory / name).is_file():
                    raise ValueError("Missing weight shard")

        files = {}
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                if path.is_symlink() or path.stat().st_size == 0:
                    raise ValueError("Invalid snapshot file")
                files[path.name] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
        manifest = {"model_id": model_id, "revision": revision, "files": files}
        temporary = directory / (MARKER + ".tmp")
        temporary.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        temporary.replace(marker)
        emit("model_download_complete")


def main():
    try:
        prepare_model(
            os.environ.get("MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct"),
            os.environ.get("MODEL_REVISION", ""),
            os.environ.get("MODEL_DIR", "/models/qwen"),
        )
    except Exception as error:
        emit("model_initialization_failed", **safe_error_metadata(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
