"""
HTTP Reranker Client for Graphiti.
Supports generic OpenAI-compatible /v1/rerank endpoints (e.g. SiliconFlow, Jina, BGE).
"""

import logging

import httpx
from graphiti_core.cross_encoder.client import CrossEncoderClient

logger = logging.getLogger(__name__)

class HttpRerankerClient(CrossEncoderClient):
    def __init__(self, api_key: str, base_url: str, model: str, dimensions: int | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.dimensions = dimensions
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        """
        Rank the given passages based on their relevance to the query.
        
        Args:
            query (str): The query string.
            passages (list[str]): A list of passages to rank.
            
        Returns:
            list[tuple[str, float]]: A list of tuples containing the passage and its score,
                                     sorted in descending order of relevance.
        """
        if not passages:
            return []

        # Construct the full URL
        # Some providers use /v1/rerank, others might use just /rerank if base_url includes /v1
        # The user provided `https://api.siliconflow.cn/v1` as base_url.
        # The curl example shows `https://api.siliconflow.cn/v1/rerank`.
        # So we append /rerank.
        url = f"{self.base_url}/rerank"
        
        payload = {
            "model": self.model,
            "query": query,
            "documents": passages,
            "top_n": len(passages)
        }
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                
                # SiliconFlow/BGE format:
                # { "results": [ { "index": 0, "relevance_score": 0.9 }, ... ] }
                # Some APIs return "documents" instead of "results", or simple list of scores.
                # Assuming standard BGE/Jina format which returns 'results' list with 'index' and 'relevance_score'.
                
                results = data.get("results", [])
                
                # Map results back to passages
                ranked_passages = []
                for res in results:
                    idx = res.get("index")
                    score = res.get("relevance_score")
                    
                    if idx is not None and 0 <= idx < len(passages):
                        # Ensure score is float
                        try:
                            score = float(score)
                        except (ValueError, TypeError):
                            score = 0.0
                            
                        ranked_passages.append((passages[idx], score))
                
                # If the API didn't return all documents (e.g. top_n limit applied by API despite our request),
                # we only return what was returned.
                
                # Sort by score descending
                ranked_passages.sort(key=lambda x: x[1], reverse=True)
                
                return ranked_passages

            except Exception as e:
                logger.error(f"Error during HTTP reranking: {e}")
                # Fallback: return original passages with 0 score
                return [(p, 0.0) for p in passages]
