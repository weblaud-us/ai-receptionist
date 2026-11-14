"""Base repository: tenant_id on every operation, impossible to forget."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantRepo:
    collection: str = ""

    def __init__(self, db: Any, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        if not self.collection:
            raise ValueError("collection not set")
        self._c = db[self.collection]
        self._t = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._t

    @property
    def raw(self) -> Any:
        """The underlying collection — for aggregations; callers must `$match` on `tenant_id` first."""
        return self._c

    def _q(self, q: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {**(q or {}), "tenant_id": self._t}          # tenant_id always wins

    def _match(self) -> Dict[str, Any]:
        return {"$match": {"tenant_id": self._t}}

    async def find_one(self, q: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        return await self._c.find_one(self._q(q), projection)

    async def find(self, q: Optional[Dict[str, Any]] = None, *, sort: Optional[List[tuple]] = None,
                   skip: int = 0, limit: int = 200, projection: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        cursor = self._c.find(self._q(q), projection)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        cursor = cursor.limit(limit)
        return await cursor.to_list(length=limit)

    async def count(self, q: Optional[Dict[str, Any]] = None) -> int:
        return await self._c.count_documents(self._q(q))

    async def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc = {**doc, "tenant_id": self._t}
        doc.setdefault("created_at", utcnow())
        await self._c.insert_one(doc)
        return doc

    async def insert_many(self, docs: List[Dict[str, Any]], *, ordered: bool = True) -> None:
        now = utcnow()
        rows = [{**d, "tenant_id": self._t, "created_at": d.get("created_at", now)} for d in docs]
        if rows:
            await self._c.insert_many(rows, ordered=ordered)

    async def update_one(self, q: Dict[str, Any], update: Dict[str, Any], *, upsert: bool = False) -> Any:
        update = dict(update)
        if upsert:
            update.setdefault("$setOnInsert", {})
            update["$setOnInsert"] = {**update["$setOnInsert"], "tenant_id": self._t}
        if "$set" in update:
            update["$set"] = {k: v for k, v in update["$set"].items() if k != "tenant_id"}
        return await self._c.update_one(self._q(q), update, upsert=upsert)

    async def update_many(self, q: Dict[str, Any], update: Dict[str, Any]) -> Any:
        return await self._c.update_many(self._q(q), update)

    async def delete_one(self, q: Dict[str, Any]) -> Any:
        return await self._c.delete_one(self._q(q))

    async def delete_many(self, q: Dict[str, Any]) -> Any:
        return await self._c.delete_many(self._q(q))

    async def aggregate(self, pipeline: List[Dict[str, Any]], *, limit: int = 1000) -> List[Dict[str, Any]]:
        return await self._c.aggregate([self._match(), *pipeline]).to_list(length=limit)
