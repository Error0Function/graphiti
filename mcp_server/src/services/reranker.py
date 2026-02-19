"""
HTTP Reranker Client for Graphiti.
Supports generic OpenAI-compatible /v1/rerank endpoints (e.g. SiliconFlow, Jina, BGE).
"""

import logging

import httpx
from graphiti_core.cross_encoder.client import CrossEncoderClient

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
REQUEST_TIMEOUT = 30.0


class HttpRerankerClient(CrossEncoderClient):
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Reuse a single httpx.AsyncClient across calls."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=REQUEST_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying httpx client to release connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        """
        Rank the given passages based on their relevance to the query.

        Args:
            query: The query string.
            passages: A list of passages to rank.

        Returns:
            A list of (passage, score) tuples sorted in descending order of relevance.

        Raises:
            RuntimeError: If the reranking request fails after retries.
        """
        if not passages:
            return []

        url = f'{self.base_url}/rerank'

        payload: dict = {
            'model': self.model,
            'query': query,
            'documents': passages,
            'top_n': len(passages),
        }

        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

                # Standard BGE/Jina/SiliconFlow format:
                # { "results": [ { "index": 0, "relevance_score": 0.9 }, ... ] }
                results = data.get('results', [])

                ranked_passages: list[tuple[str, float]] = []
                for res in results:
                    idx = res.get('index')
                    score = res.get('relevance_score')

                    if idx is not None and 0 <= idx < len(passages):
                        try:
                            score = float(score)
                        except (ValueError, TypeError):
                            score = 0.0
                        ranked_passages.append((passages[idx], score))

                ranked_passages.sort(key=lambda x: x[1], reverse=True)
                return ranked_passages

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f'Reranking attempt {attempt + 1}/{MAX_RETRIES + 1} failed: {e}. '
                        f'Retrying...'
                    )
                    continue
                break

        logger.error(f'HTTP reranking failed after {MAX_RETRIES + 1} attempts: {last_error}')
        raise RuntimeError(
            f'Reranking request failed after {MAX_RETRIES + 1} attempts'
        ) from last_error
