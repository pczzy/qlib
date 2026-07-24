#!/usr/bin/env python3
"""Deterministic training provenance for Qlib rolling recorders."""
from __future__ import annotations

import hashlib
import json
import struct
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


FIELDS = ("open", "high", "low", "close", "volume", "amount", "vwap", "factor", "adjclose", "change")
ROOT = Path(__file__).resolve().parent
PROVENANCE_SCHEMA = 1


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_provenance(provider: Path) -> dict[str, str]:
    state_path = ROOT / "state/pipeline-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    sina_path = provider / "sina_incremental.json"
    return {
        "github_archive_sha256": state.get("archive_sha256", ""),
        "sina_manifest_sha256": file_hash(sina_path) if sina_path.exists() else "",
    }


def code_hash() -> str:
    digest = hashlib.sha256()
    paths = [
        ROOT / "train_all.sh",
        ROOT / "verify_models.py",
        ROOT / "model_provenance.py",
        *sorted((ROOT / "qlibAssistant/roll").glob("*.py")),
    ]
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _calendar(provider: Path) -> list[str]:
    return [line.strip() for line in (provider / "calendars/day.txt").read_text().splitlines() if line.strip()]


def _slice_bounds(calendar: list[str], start: str, end: str) -> tuple[int, int]:
    indexes = [index for index, day in enumerate(calendar) if start <= day <= end]
    if not indexes:
        raise ValueError(f"no calendar dates in slice {start}..{end}")
    return indexes[0], indexes[-1]


def _symbols_for_slice(provider: Path, start: str, end: str) -> list[str]:
    symbols = set()
    for line in (provider / "instruments/csi300.txt").read_text().splitlines():
        symbol, member_start, member_end = line.split("\t")
        if member_start <= end and member_end >= start:
            symbols.add(symbol.lower())
    return sorted(symbols)


@lru_cache(maxsize=32)
def data_slice_hash(provider_text: str, start: str, end: str) -> str:
    """Hash exact raw Qlib values available to CSI300 for a date slice."""
    provider = Path(provider_text).resolve()
    calendar = _calendar(provider)
    first, last = _slice_bounds(calendar, start, end)
    symbols = _symbols_for_slice(provider, start, end)
    digest = hashlib.sha256()
    digest.update(f"schema={PROVENANCE_SCHEMA}\n{start}\n{end}\n".encode())
    digest.update("\n".join(calendar[first : last + 1]).encode())
    for symbol in symbols:
        digest.update(f"\n{symbol}\n".encode())
        for field in FIELDS:
            path = provider / "features" / symbol / f"{field}.day.bin"
            digest.update(field.encode() + b"\0")
            if not path.exists():
                digest.update(b"MISSING")
                continue
            with path.open("rb") as handle:
                header = handle.read(4)
                if len(header) != 4:
                    digest.update(b"INVALID")
                    continue
                file_start = int(struct.unpack("<f", header)[0])
                value_first = max(first, file_start)
                value_last = min(last, file_start + path.stat().st_size // 4 - 2)
                digest.update(struct.pack("<ii", value_first, value_last))
                if value_first <= value_last:
                    handle.seek(4 + (value_first - file_start) * 4)
                    remaining = (value_last - value_first + 1) * 4
                    while remaining:
                        block = handle.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ValueError(f"short feature file: {path}")
                        digest.update(block)
                        remaining -= len(block)
    return digest.hexdigest()


def build_provenance(task: dict, provider_uri: str, started_utc: str = "", finished_utc: str = "") -> dict:
    provider = Path(provider_uri).resolve()
    segments = {
        key: [str(value) for value in task["dataset"]["kwargs"]["segments"][key]]
        for key in ("train", "valid", "test")
    }
    train_hash = data_slice_hash(str(provider), *segments["train"])
    valid_hash = data_slice_hash(str(provider), *segments["valid"])
    sources = source_provenance(provider)
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "segments": segments,
        "train_data_slice_sha256": train_hash,
        "valid_data_slice_sha256": valid_hash,
        "training_data_sha256": canonical_hash({"train": train_hash, "valid": valid_hash}),
        **sources,
        "feature_config_sha256": canonical_hash(task["dataset"]),
        "model_config_sha256": canonical_hash(task["model"]),
        "task_config_sha256": canonical_hash(task),
        "code_sha256": code_hash(),
        "training_started_utc": started_utc,
        "training_finished_utc": finished_utc,
    }
    # Source hashes are recorded for lineage, but the reusable identity is tied
    # to the exact train/valid bytes.  Appending test-only Sina dates or a new
    # GitHub container must not invalidate an unchanged learning matrix.
    identity = {
        key: payload[key]
        for key in (
            "schema",
            "segments",
            "train_data_slice_sha256",
            "valid_data_slice_sha256",
            "training_data_sha256",
            "feature_config_sha256",
            "model_config_sha256",
            "task_config_sha256",
            "code_sha256",
        )
    }
    payload["recorder_fingerprint_sha256"] = canonical_hash(identity)
    return payload


def expected_fingerprint(task: dict, provider_uri: str) -> str:
    return build_provenance(task, provider_uri)["recorder_fingerprint_sha256"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
