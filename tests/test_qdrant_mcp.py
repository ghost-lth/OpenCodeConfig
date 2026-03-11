import asyncio
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio


PACKAGE_PATH = "/Volumes/ExternalSSD/.cache/uv/archive-v0/UqgBgCTMAMTfsvdI5ioNS/lib/python3.10/site-packages"
if PACKAGE_PATH not in sys.path:
    sys.path.insert(0, PACKAGE_PATH)

import mcp_server_qdrant.qdrant as qdrant  # type: ignore


class FakeEmbeddingProvider:
    def __init__(self):
        self.vector_name = "vec"
        self.vector_size = 2

    async def embed_documents(self, documents):
        return [[0.1, 0.2] for _ in documents]

    async def embed_query(self, query):
        return [0.1, 0.2]

    def get_vector_name(self):
        return self.vector_name

    def get_vector_size(self):
        return self.vector_size


class FakeModels:
    class Distance:
        COSINE = "cosine"

    class VectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    class PointStruct:
        def __init__(self, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    class PointIdsList:
        def __init__(self, points):
            self.points = points

    class Filter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class FakePoint:
    def __init__(self, id, payload):
        self.id = id
        self.payload = payload


class FakeCollectionsResponse:
    def __init__(self, names):
        self.collections = [SimpleNamespace(name=n) for n in names]


class FakeQueryResponse:
    def __init__(self, points):
        self.points = points


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.collections = {}
        self.last_score_threshold = None

    async def get_collections(self):
        return FakeCollectionsResponse(list(self.collections.keys()))

    async def collection_exists(self, name):
        return name in self.collections

    async def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = {}

    async def create_payload_index(self, collection_name, field_name, field_schema):
        return None

    async def upsert(self, collection_name, points):
        for p in points:
            self.collections.setdefault(collection_name, {})[p.id] = {
                "payload": p.payload,
            }

    async def query_points(
        self,
        collection_name,
        query,
        using,
        limit,
        query_filter=None,
        score_threshold=None,
    ):
        self.last_score_threshold = score_threshold
        coll = self.collections.get(collection_name, {})
        pts = [FakePoint(id=k, payload=v["payload"]) for k, v in coll.items()]
        return FakeQueryResponse(pts[:limit])

    async def delete(self, collection_name, points_selector):
        coll = self.collections.get(collection_name, {})
        for pid in points_selector.points:
            coll.pop(pid, None)


def setup_module(_module):
    # Monkeypatch qdrant models and client to fakes for all tests
    qdrant.models = FakeModels  # type: ignore
    qdrant.AsyncQdrantClient = FakeClient  # type: ignore


@pytest_asyncio.fixture(scope="function")
async def connector_with_threshold():
    return qdrant.QdrantConnector(
        qdrant_url=None,
        qdrant_api_key=None,
        collection_name="test",
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_local_path=None,
        field_indexes=None,
        score_threshold=0.35,
    )


@pytest.mark.asyncio
async def test_store_search_includes_point_id_and_threshold(connector_with_threshold):
    connector = connector_with_threshold
    connector = qdrant.QdrantConnector(
        qdrant_url=None,
        qdrant_api_key=None,
        collection_name="test",
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_local_path=None,
        field_indexes=None,
        score_threshold=0.35,
    )

    await connector.store(qdrant.Entry(content="hello", metadata={"k": "v"}))
    results = await connector.search("hello", collection_name="test", limit=5)

    assert len(results) == 1
    assert results[0].content == "hello"
    assert results[0].metadata == {"k": "v"}
    assert results[0].point_id is not None
    assert connector._client.last_score_threshold == 0.35  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_update_replaces_payload():
    connector = qdrant.QdrantConnector(
        qdrant_url=None,
        qdrant_api_key=None,
        collection_name="test",
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_local_path=None,
        field_indexes=None,
        score_threshold=None,
    )

    await connector.store(qdrant.Entry(content="v1", metadata={"v": 1}))
    first = (await connector.search("v1", collection_name="test"))[0]

    await connector.update(
        point_id=first.point_id,
        entry=qdrant.Entry(content="v2", metadata={"v": 2}),
        collection_name="test",
    )

    updated = (await connector.search("v2", collection_name="test"))[0]
    assert updated.content == "v2"
    assert updated.metadata == {"v": 2}
    assert updated.point_id == first.point_id


@pytest.mark.asyncio
async def test_delete_removes_point():
    connector = qdrant.QdrantConnector(
        qdrant_url=None,
        qdrant_api_key=None,
        collection_name="test",
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_local_path=None,
        field_indexes=None,
        score_threshold=None,
    )

    await connector.store(qdrant.Entry(content="keep", metadata=None))
    await connector.store(qdrant.Entry(content="drop", metadata=None))

    points = await connector.search("", collection_name="test", limit=10)
    to_delete = next(p for p in points if p.content == "drop")

    await connector.delete(point_id=to_delete.point_id, collection_name="test")

    remaining = await connector.search("", collection_name="test", limit=10)
    contents = [p.content for p in remaining]
    assert "drop" not in contents
    assert "keep" in contents
