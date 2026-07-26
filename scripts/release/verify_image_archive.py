#!/usr/bin/env python3
"""Reject a server image when any historical layer contains business source."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import PurePosixPath
from typing import Any


BUSINESS_ROOTS = (
    PurePosixPath("app/app"),
    PurePosixPath("app/migrations"),
    PurePosixPath("app/Stages"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--image", required=True)
    return parser.parse_args()


def _read_json(archive: tarfile.TarFile, member_name: str) -> Any:
    member = archive.getmember(member_name)
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"archive member is unreadable: {member_name}")
    return json.load(stream)


def _digest_member(digest: str) -> str:
    algorithm, separator, value = digest.partition(":")
    if separator != ":" or algorithm != "sha256" or not value:
        raise ValueError(f"unsupported image digest: {digest}")
    return f"blobs/sha256/{value}"


def _image_aliases(image: str) -> set[str]:
    aliases = {image}
    first_component = image.split("/", maxsplit=1)[0]
    has_registry = "/" in image and (
        "." in first_component or ":" in first_component or first_component == "localhost"
    )
    if not has_registry:
        prefix = "docker.io/" if "/" in image else "docker.io/library/"
        aliases.add(f"{prefix}{image}")
    return aliases


def _descriptor_layers(archive: tarfile.TarFile, descriptor: dict[str, Any]) -> list[str]:
    payload = _read_json(archive, _digest_member(descriptor["digest"]))
    if "layers" in payload:
        return [_digest_member(layer["digest"]) for layer in payload["layers"]]
    layers: list[str] = []
    for child in payload.get("manifests", []):
        annotations = child.get("annotations", {})
        if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
            continue
        layers.extend(_descriptor_layers(archive, child))
    return layers


def _oci_layers(archive: tarfile.TarFile, image: str) -> list[str]:
    index = _read_json(archive, "index.json")
    aliases = _image_aliases(image)
    descriptors = [
        descriptor
        for descriptor in index.get("manifests", [])
        if aliases.intersection(descriptor.get("annotations", {}).values())
    ]
    if not descriptors:
        raise ValueError(f"image reference is absent from archive index: {image}")

    layers: list[str] = []
    for descriptor in descriptors:
        layers.extend(_descriptor_layers(archive, descriptor))
    if not layers:
        raise ValueError(f"image manifest contains no layers: {image}")
    return layers


def _legacy_layers(archive: tarfile.TarFile, image: str) -> list[str]:
    manifests = _read_json(archive, "manifest.json")
    matches = [manifest for manifest in manifests if image in manifest.get("RepoTags", [])]
    if not matches:
        raise ValueError(f"image reference is absent from archive manifest: {image}")
    layers = [layer for manifest in matches for layer in manifest.get("Layers", [])]
    if not layers:
        raise ValueError(f"image manifest contains no layers: {image}")
    return layers


def _normalized_member(name: str) -> PurePosixPath:
    normalized = name
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized.lstrip("/"))


def _is_business_source(name: str) -> bool:
    path = _normalized_member(name)
    if path.suffix != ".py":
        return False
    return any(path == root or root in path.parents for root in BUSINESS_ROOTS)


def verify(archive_path: str, image: str) -> None:
    with tarfile.open(archive_path, mode="r:*") as archive:
        names = set(archive.getnames())
        if "index.json" in names:
            layer_names = _oci_layers(archive, image)
        elif "manifest.json" in names:
            layer_names = _legacy_layers(archive, image)
        else:
            raise ValueError("unsupported image archive: no OCI or Docker manifest")

        for layer_name in layer_names:
            member = archive.getmember(layer_name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"image layer is unreadable: {layer_name}")
            with tarfile.open(fileobj=stream, mode="r:*") as layer:
                for layer_member in layer:
                    if _is_business_source(layer_member.name):
                        normalized = _normalized_member(layer_member.name)
                        raise ValueError(
                            "business Python source exists in an image layer: "
                            f"{normalized}"
                        )


def main() -> int:
    args = parse_args()
    try:
        verify(args.archive, args.image)
    except (KeyError, OSError, tarfile.TarError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
