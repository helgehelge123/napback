"""Read-only TrueNAS app export; also executed standalone over authenticated SSH.

All configuration contents, including secrets, stay in memory until the caller
encrypts the returned archive. No containers are started or modified here.
"""

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

MAX_EXPORT = 64 * 1024 * 1024
MAX_CONTENT = 256 * 1024 * 1024
MAX_FILES = 50000


def command(argv):
    result = subprocess.run(argv, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("App configuration command failed")
    return result.stdout


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def config_files(root):
    app_root = root / "app_configs"
    if not app_root.is_dir() or app_root.is_symlink():
        raise ValueError("TrueNAS app configuration directory unavailable")
    paths = []

    def unreadable(error):
        raise error

    for directory, folders, files in os.walk(app_root, followlinks=False, onerror=unreadable):
        for name in folders + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("Symlinks in app configuration are not supported")
        paths.extend(Path(directory) / name for name in files)
    for name in ("metadata.yaml", "user_config.yaml"):
        path = root / name
        if path.exists() or path.is_symlink():
            paths.append(path)
    if len(paths) > MAX_FILES:
        raise ValueError("Too many app configuration files")
    return sorted(paths)


def containers(run):
    identifiers = sorted(run(["docker", "ps", "-aq"]).decode().split())
    return (
        json.loads(run(["docker", "inspect", "--type", "container", *identifiers]))
        if identifiers
        else []
    )


def container_signature(items):
    # Runtime statistics change continuously; deployment configuration must not.
    fields = ("Id", "Name", "Image", "Config", "HostConfig", "Mounts")
    result = []
    for item in items:
        entry = {key: item.get(key) for key in fields}
        # Docker builds this array from a map and may reorder it between reads.
        entry["Mounts"] = sorted(entry["Mounts"] or [], key=lambda mount: mount["Destination"])
        result.append(entry)
    return sorted(result, key=lambda entry: entry["Id"])


def export(root=Path("/mnt/.ix-apps"), run=command):
    root = Path(root)
    paths = config_files(root)
    deployed = containers(run)
    output = io.BytesIO()
    inventory = []
    observed = {}
    total = 0
    compose_checks = []

    with tarfile.open(fileobj=output, mode="w:gz") as archive:

        def add(name, content, mode=0o600, uid=0, gid=0):
            nonlocal total
            total += len(content)
            if total > MAX_CONTENT or len(inventory) >= MAX_FILES:
                raise ValueError("App configuration export exceeds limit")
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid = len(content), mode, uid, gid
            archive.addfile(info, io.BytesIO(content))
            inventory.append(
                {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            )

        def add_json(name, value):
            add(name, json.dumps(value, sort_keys=True, indent=2).encode())

        def add_file(path):
            path = Path(path).absolute()
            if path in observed:
                return
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_EXPORT:
                    raise ValueError("Unsupported or oversized app configuration file")
                content = stream.read(MAX_EXPORT + 1)
                after = os.fstat(stream.fileno())
            if len(content) != before.st_size or signature(before) != signature(after):
                raise ValueError("App configuration changed during export")
            observed[path] = signature(after)
            add(
                "files/" + path.as_posix().lstrip("/"),
                content,
                stat.S_IMODE(before.st_mode),
                before.st_uid,
                before.st_gid,
            )

        for path in paths:
            add_file(path)
        add_json("docker/containers.json", deployed)
        images = sorted({item["Image"] for item in deployed})
        add_json(
            "docker/images.json",
            json.loads(run(["docker", "image", "inspect", *images])) if images else [],
        )
        for kind in ("network", "volume"):
            names = sorted(run(["docker", kind, "ls", "-q"]).decode().split())
            add_json(
                f"docker/{kind}s.json",
                json.loads(run(["docker", kind, "inspect", *names])) if names else [],
            )

        projects = set()
        for item in deployed:
            labels = item.get("Config", {}).get("Labels") or {}
            filenames = labels.get("com.docker.compose.project.config_files", "")
            if not filenames:
                continue
            files = tuple(Path(p).absolute() for p in filenames.split(","))
            if all(root == p or root in p.parents for p in files):
                continue  # Full original TrueNAS definitions already included.
            working = labels.get("com.docker.compose.project.working_dir")
            if not working or not Path(working).is_absolute():
                raise ValueError("External Compose project needs an absolute working directory")
            projects.add((working, files))
        external = []
        for index, (working, files) in enumerate(sorted(projects)):
            argv = ["docker", "compose", "--project-directory", working]
            for path in files:
                add_file(path)
                argv += ["-f", str(path)]
            env = Path(working) / ".env"
            if env.exists() or env.is_symlink():
                add_file(env)
            argv += ["config", "--format", "json"]
            resolved = json.loads(run(argv))
            references = json.loads(run([*argv, "--no-env-resolution"]))
            for service in references.get("services", {}).values():
                entries = service.get("env_file", [])
                for entry in [entries] if isinstance(entries, str) else entries:
                    path = Path(entry if isinstance(entry, str) else entry["path"])
                    path = path if path.is_absolute() else Path(working) / path
                    if (
                        isinstance(entry, dict)
                        and not entry.get("required", True)
                        and not path.exists()
                    ):
                        continue
                    add_file(path)
            for kind in ("secrets", "configs"):
                for entry in resolved.get(kind, {}).values():
                    if entry.get("file"):
                        path = Path(entry["file"])
                        add_file(path if path.is_absolute() else Path(working) / path)
            name = f"compose/{index}/resolved.json"
            add_json(name, resolved)
            external.append(
                {"working_directory": working, "files": [str(p) for p in files], "resolved": name}
            )
            compose_checks.append((argv, resolved))
        add_json("compose/projects.json", external)
        add_json(
            "system.json",
            {
                "truenas_version": run(["midclt", "call", "system.version"])
                .decode()
                .strip()
                .strip('"'),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        if config_files(root) != paths or any(
            signature(p.lstat()) != expected for p, expected in observed.items()
        ):
            raise ValueError("App configuration changed during export")
        if container_signature(containers(run)) != container_signature(deployed):
            raise ValueError("Containers changed during export")
        if any(json.loads(run(argv)) != expected for argv, expected in compose_checks):
            raise ValueError("Compose configuration changed during export")
        manifest = json.dumps(
            {"format": 1, "app_root": str(root), "files": inventory}, sort_keys=True
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size, info.mode = len(manifest), 0o600
        archive.addfile(info, io.BytesIO(manifest))
    data = output.getvalue()
    if len(data) > MAX_EXPORT:
        raise ValueError("Compressed app configuration export exceeds limit")
    return data


if __name__ == "__main__":
    try:
        sys.stdout.buffer.write(export())
    except Exception:
        # Never print command stderr, configuration values, or secret-bearing paths.
        sys.stderr.write("TrueNAS app configuration export failed; no archive produced.\n")
        sys.exit(1)
