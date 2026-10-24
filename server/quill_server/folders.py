"""Folders: a tree (``parent_folder_id``) holding meetings."""

from __future__ import annotations

from typing import Any

from .db import Database
from .errors import bad_request, not_found
from .util import iso, new_id, normalize_id


def canonical_folder_id(value: str) -> str | None:
    return normalize_id(value, "fol")


class Folders:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, folder_id: str) -> dict[str, Any] | None:
        cid = canonical_folder_id(folder_id)
        return self._db.one("SELECT * FROM folders WHERE id=?", (cid,)) if cid else None

    def require(self, folder_id: str) -> dict[str, Any]:
        row = self.get(folder_id)
        if row is None:
            raise not_found("folder")
        return row

    def create(self, name: str, parent_folder_id: str | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise bad_request("name is required")
        parent = self.require(parent_folder_id)["id"] if parent_folder_id else None
        fid = new_id("fol")
        self._db.execute(
            "INSERT INTO folders(id, name, parent_folder_id, created_at) VALUES (?,?,?,?)",
            (fid, name, parent, iso()),
        )
        return self._db.one("SELECT * FROM folders WHERE id=?", (fid,)) or {}

    def delete(self, folder_id: str) -> None:
        """Deleting a folder re-parents its children to the deleted folder's parent."""
        row = self.require(folder_id)
        with self._db.transaction():
            self._db.execute(
                "UPDATE folders SET parent_folder_id=? WHERE parent_folder_id=?",
                (row["parent_folder_id"], row["id"]),
            )
            self._db.execute("DELETE FROM folders WHERE id=?", (row["id"],))

    def list_all(self) -> list[dict[str, Any]]:
        """Alphabetical (case-insensitive), with meeting counts."""
        return self._db.query(
            "SELECT f.*, (SELECT COUNT(*) FROM meeting_folders mf WHERE mf.folder_id=f.id) AS meeting_count "
            "FROM folders f ORDER BY LOWER(f.name), f.id"
        )

    def add_meeting(self, folder_id: str, meeting_id: str) -> None:
        row = self.require(folder_id)
        self._db.execute(
            "INSERT OR IGNORE INTO meeting_folders(folder_id, meeting_id) VALUES (?,?)", (row["id"], meeting_id)
        )

    def remove_meeting(self, folder_id: str, meeting_id: str) -> None:
        row = self.require(folder_id)
        self._db.execute("DELETE FROM meeting_folders WHERE folder_id=? AND meeting_id=?", (row["id"], meeting_id))

    def descendants(self, folder_id: str) -> set[str]:
        """The folder and every folder below it."""
        rows = self._db.query(
            "WITH RECURSIVE t(id) AS (SELECT ? UNION SELECT f.id FROM folders f JOIN t ON f.parent_folder_id=t.id) "
            "SELECT id FROM t",
            (folder_id,),
        )
        return {r["id"] for r in rows}

    def ancestors(self, folder_id: str) -> set[str]:
        """The folder and every folder above it."""
        rows = self._db.query(
            "WITH RECURSIVE t(id) AS (SELECT ? UNION SELECT f.parent_folder_id FROM folders f JOIN t ON f.id=t.id "
            "WHERE f.parent_folder_id IS NOT NULL) SELECT id FROM t",
            (folder_id,),
        )
        return {r["id"] for r in rows}

    def of_meeting(self, meeting_id: str, *, with_ancestors: bool = False) -> list[dict[str, Any]]:
        direct = self._db.query(
            "SELECT f.* FROM folders f JOIN meeting_folders mf ON mf.folder_id=f.id WHERE mf.meeting_id=?",
            (meeting_id,),
        )
        if not with_ancestors:
            return sorted(direct, key=lambda r: (r["name"].lower(), r["id"]))
        ids: set[str] = set()
        for r in direct:
            ids |= self.ancestors(r["id"])
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self._db.query(f"SELECT * FROM folders WHERE id IN ({marks}) ORDER BY LOWER(name), id", list(ids))

    def ids_of_meeting_with_ancestors(self, meeting_id: str) -> set[str]:
        return {r["id"] for r in self.of_meeting(meeting_id, with_ancestors=True)}


