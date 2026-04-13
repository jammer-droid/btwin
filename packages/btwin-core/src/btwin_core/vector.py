"""Vector store for semantic search using ChromaDB."""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb


class VectorStore:
    _SEARCH_CACHE_MAX = 128
    _SYNC_STAMP = ".btwin-vector-sync"

    def __init__(self, persist_dir: Path) -> None:
        self._persist_dir = persist_dir
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._sync_stamp_path = self._persist_dir / self._SYNC_STAMP
        self._sync_stamp_path.touch(exist_ok=True)
        self._client: chromadb.ClientAPI | None = None
        self._collection: Any | None = None
        self._search_cache: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()
        self._last_sync_version = 0
        self._reopen_collection()
        self._last_sync_version = self._sync_version()

    def add(self, doc_id: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        self._refresh_if_stale()
        self._collection.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata] if metadata else None,
        )
        self._search_cache.clear()
        self._touch_sync_stamp()

    def search(
        self,
        query: str,
        n_results: int = 3,
        metadata_filters: dict[str, str] | None = None,
        *,
        hybrid: bool = True,
        lexical_weight: float = 0.4,
        recency_half_life_days: float = 30.0,
        mmr_lambda: float = 0.75,
        candidate_multiplier: int = 4,
    ) -> list[dict]:
        self._refresh_if_stale()
        if self._collection.count() == 0:
            return []

        n_results = max(1, min(n_results, self._collection.count()))
        lexical_weight = min(1.0, max(0.0, lexical_weight))
        mmr_lambda = min(1.0, max(0.0, mmr_lambda))
        candidate_multiplier = max(1, candidate_multiplier)

        cache_key = (
            query,
            n_results,
            tuple(sorted((metadata_filters or {}).items())),
            hybrid,
            round(lexical_weight, 4),
            round(recency_half_life_days, 4),
            round(mmr_lambda, 4),
            candidate_multiplier,
        )
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached]

        vector_candidates = self._vector_candidates(
            query=query,
            n_results=n_results * candidate_multiplier,
            metadata_filters=metadata_filters,
        )
        if not vector_candidates:
            return []

        candidate_ids = [item["id"] for item in vector_candidates]
        lexical_scores = self._lexical_scores(query, candidate_ids)

        scored: list[dict[str, Any]] = []
        for item in vector_candidates:
            vector_score = self._distance_to_similarity(item.get("distance"))
            lexical_score = lexical_scores.get(item["id"], 0.0)
            relevance = vector_score
            if hybrid:
                relevance = (1.0 - lexical_weight) * vector_score + lexical_weight * lexical_score

            recency = self._recency_score(item.get("metadata") or {}, recency_half_life_days)
            total_score = relevance * recency

            enriched = dict(item)
            enriched.update(
                {
                    "_score": total_score,
                    "_relevance": relevance,
                    "_recency": recency,
                }
            )
            scored.append(enriched)

        ranked = sorted(scored, key=lambda x: x["_score"], reverse=True)
        selected = self._mmr_select(ranked, n_results=n_results, mmr_lambda=mmr_lambda)

        output: list[dict[str, Any]] = []
        for item in selected:
            payload = {
                "id": item["id"],
                "content": item["content"],
                "metadata": item.get("metadata") or {},
                "distance": item.get("distance"),
            }
            output.append(payload)

        self._search_cache[cache_key] = [dict(item) for item in output]
        while len(self._search_cache) > self._SEARCH_CACHE_MAX:
            self._search_cache.popitem(last=False)
        return output

    def delete(self, doc_id: str) -> None:
        self._refresh_if_stale()
        self._collection.delete(ids=[doc_id])
        self._search_cache.clear()
        self._touch_sync_stamp()

    def has(self, doc_id: str) -> bool:
        self._refresh_if_stale()
        result = self._collection.get(ids=[doc_id], include=[])
        return bool(result.get("ids"))

    def count(self) -> int:
        self._refresh_if_stale()
        return self._collection.count()

    def list_ids(self) -> set[str]:
        self._refresh_if_stale()
        result = self._collection.get(include=[])
        ids = result.get("ids") or []
        return set(ids)

    def _reopen_collection(self) -> None:
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        try:
            existing = self._client.get_collection(name="btwin_entries")
            if (existing.metadata or {}).get("hnsw:space") != "cosine":
                self._client.delete_collection(name="btwin_entries")
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name="btwin_entries",
            metadata={"hnsw:space": "cosine"},
        )

    def _sync_version(self) -> int:
        if not self._sync_stamp_path.exists():
            self._sync_stamp_path.touch(exist_ok=True)
        return self._sync_stamp_path.stat().st_mtime_ns

    def _touch_sync_stamp(self) -> None:
        self._sync_stamp_path.touch(exist_ok=True)
        self._last_sync_version = self._sync_version()

    def _refresh_if_stale(self) -> None:
        current_version = self._sync_version()
        if current_version <= self._last_sync_version:
            return
        self._reopen_collection()
        self._search_cache.clear()
        self._last_sync_version = current_version

    def _vector_candidates(
        self,
        query: str,
        n_results: int,
        metadata_filters: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        n_results = min(n_results, self._collection.count())
        query_args: dict[str, Any] = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if metadata_filters:
            if len(metadata_filters) > 1:
                query_args["where"] = {"$and": [{k: v} for k, v in metadata_filters.items()]}
            else:
                query_args["where"] = metadata_filters

        results = self._collection.query(**query_args)
        output = []
        for i, doc_id in enumerate(results["ids"][0]):
            output.append(
                {
                    "id": doc_id,
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] and results["metadatas"][0] else {},
                    "distance": results["distances"][0][i] if results["distances"] else None,
                }
            )
        return output

    def _lexical_scores(self, query: str, candidate_ids: list[str]) -> dict[str, float]:
        query_tokens = self._tokenize(query)
        if not query_tokens or not candidate_ids:
            return {}

        result = self._collection.get(ids=candidate_ids, include=["documents"])

        ids = result.get("ids") or []
        docs = result.get("documents") or []

        scores: dict[str, float] = {}
        for i, doc_id in enumerate(ids):
            content = docs[i] if i < len(docs) else ""
            tokens = self._tokenize(content)
            if not tokens:
                continue
            overlap = len(query_tokens & tokens)
            score = overlap / len(query_tokens)
            if score > 0:
                scores[doc_id] = score
        return scores

    @staticmethod
    def _distance_to_similarity(distance: float | None) -> float:
        if distance is None:
            return 0.0
        return 1.0 / (1.0 + max(0.0, float(distance)))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9가-힣]+", text.lower()) if len(token) > 1}

    @staticmethod
    def _recency_score(metadata: dict[str, Any], half_life_days: float) -> float:
        if half_life_days <= 0:
            return 1.0

        candidate = metadata.get("created_at") or metadata.get("date")
        if not candidate:
            path = str(metadata.get("path") or "")
            match = re.search(r"(\d{4}-\d{2}-\d{2})", path)
            if match:
                candidate = match.group(1)
        if not candidate:
            return 1.0

        dt = VectorStore._parse_datetime(str(candidate))
        if dt is None:
            return 1.0

        now = datetime.now(timezone.utc)
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        return 0.5 ** (age_days / half_life_days)

    @staticmethod
    def _parse_datetime(raw: str) -> datetime | None:
        if not raw:
            return None

        try:
            if "T" in raw:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(raw, "%Y-%m-%d")
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    def _mmr_select(self, ranked: list[dict[str, Any]], n_results: int, mmr_lambda: float) -> list[dict[str, Any]]:
        if len(ranked) <= n_results:
            return ranked

        selected: list[dict[str, Any]] = []
        remaining = ranked.copy()

        while remaining and len(selected) < n_results:
            if not selected:
                selected.append(remaining.pop(0))
                continue

            best_idx = 0
            best_score = -math.inf
            for idx, candidate in enumerate(remaining):
                relevance = candidate.get("_relevance", 0.0)
                max_similarity = max(
                    self._content_similarity(candidate.get("content", ""), chosen.get("content", ""))
                    for chosen in selected
                )
                score = mmr_lambda * relevance - (1.0 - mmr_lambda) * max_similarity
                if score > best_score:
                    best_score = score
                    best_idx = idx

            selected.append(remaining.pop(best_idx))

        return selected

    @staticmethod
    def _content_similarity(left: str, right: str) -> float:
        left_tokens = VectorStore._tokenize(left)
        right_tokens = VectorStore._tokenize(right)
        if not left_tokens or not right_tokens:
            return 0.0
        overlap = len(left_tokens & right_tokens)
        denom = math.sqrt(len(left_tokens) * len(right_tokens))
        if denom == 0:
            return 0.0
        return overlap / denom
