"""On-disk storage for catalogues, uploaded inputs, and per-source status."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from .common import CatalogError, validate
from .sources import SOURCES, Source


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.catalog_dir = data_dir / "catalogs"
        self.input_dir = data_dir / "inputs"
        self.status_path = data_dir / "status.json"
        self.lock = threading.RLock()
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.input_dir.mkdir(parents=True, exist_ok=True)

    # MARK: Status

    def status(self) -> dict[str, dict]:
        with self.lock:
            try:
                return json.loads(self.status_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

    def _update_status(self, key: str, **values) -> None:
        with self.lock:
            status = self.status()
            status.setdefault(key, {}).update(values)
            write_atomic(self.status_path, json.dumps(status, indent=1).encode("utf-8"))

    # MARK: Catalogues

    def catalog_path(self, catalog_id: str) -> Path:
        return self.catalog_dir / f"{catalog_id}.catalog.json"

    def read_catalog(self, catalog_id: str) -> tuple[bytes, str] | None:
        """The catalogue file and its strong ETag, or None when it hasn't been built yet."""
        try:
            data = self.catalog_path(catalog_id).read_bytes()
        except FileNotFoundError:
            return None
        return data, '"' + hashlib.sha256(data).hexdigest()[:32] + '"'

    def _save_catalog(self, source: Source, catalog: dict, notes: list[str]) -> dict:
        problems = validate(catalog)
        if problems:
            raise CatalogError("The catalogue failed validation: " + "; ".join(problems[:5]))
        existing = self.read_catalog(source.catalog_id)
        if existing:
            # Keep the previous publish date when nothing but the date would change, so ETags stay stable.
            previous = json.loads(existing[0])
            if previous.get("foods") == catalog["foods"]:
                catalog["catalog"]["publishedAt"] = previous["catalog"].get("publishedAt")
                catalog["catalog"]["version"] = previous["catalog"].get("version")
        data = (json.dumps(catalog, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        write_atomic(self.catalog_path(source.catalog_id), data)
        summary = {"foodCount": len(catalog["foods"]), "notes": notes}
        self._update_status(source.key, updatedAt=now_iso(), lastAttemptAt=now_iso(), lastError=None, **summary)
        return summary

    # MARK: Building

    def refresh(self, source: Source) -> dict:
        """Downloads a fetch source, or rebuilds an upload source from its stored input."""
        with self.lock:
            try:
                if source.kind == "fetch":
                    return self._save_catalog(source, source.fetch(), [])
                stored = self.input_path(source).read_bytes()
                catalog, notes = source.convert(stored)
                return self._save_catalog(source, catalog, notes)
            except FileNotFoundError as error:
                raise CatalogError(f"Nothing uploaded for {source.name} yet.") from error
            except Exception as error:
                self._update_status(source.key, lastAttemptAt=now_iso(), lastError=str(error))
                raise

    def upload(self, source: Source, raw: bytes) -> dict:
        with self.lock:
            try:
                prepared = source.prepare(raw)
                catalog, notes = source.convert(prepared)
                summary = self._save_catalog(source, catalog, notes)
                write_atomic(self.input_path(source), prepared)
                return summary
            except Exception as error:
                self._update_status(source.key, lastAttemptAt=now_iso(), lastError=str(error))
                raise

    def input_path(self, source: Source) -> Path:
        return self.input_dir / f"{source.key}.input"

    def rebuild_uploaded(self) -> None:
        """Re-converts stored uploads so converter improvements apply after an update."""
        for source in SOURCES.values():
            if source.kind == "upload" and self.input_path(source).exists():
                try:
                    self.refresh(source)
                except Exception as error:  # recorded in status; keep serving the previous file
                    print(f"[rebuild] {source.key}: {error}", flush=True)
