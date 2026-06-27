"""Tests for factory_rag tools — Pinecone upsert + prune."""
from unittest.mock import MagicMock, patch


def _make_doc(doc_type="blueprint_template", content="Test blueprint summary for real estate automation"):
    return {
        "doc_type": doc_type,
        "source_id": "dep-abc-123",
        "content": content,
        "content_hash": "sha256:abc123",
        "industry": "saas",
    }


def test_upsert_factory_documents_returns_count():
    """upsert_factory_documents should return the number of vectors upserted."""
    from agents.factory_rag.tools import upsert_factory_documents

    mock_idx = MagicMock()
    mock_idx.query.return_value = MagicMock(matches=[])

    with patch("agents.factory_rag.tools.embed", return_value=[0.1] * 512), \
         patch("agents.factory_rag.tools.pinecone_index", return_value=mock_idx):
        docs = [
            _make_doc(),
            _make_doc(doc_type="failure_lesson", content="Task failed due to missing env var"),
        ]
        count = upsert_factory_documents(docs, industry="saas")
        assert count == 2
        assert mock_idx.upsert.called


def test_upsert_factory_documents_dedup_skips_high_cosine():
    """Docs with cosine >= 0.96 should be skipped."""
    from agents.factory_rag.tools import upsert_factory_documents

    mock_match = MagicMock()
    mock_match.score = 0.97
    mock_idx = MagicMock()
    mock_idx.query.return_value = MagicMock(matches=[mock_match])

    with patch("agents.factory_rag.tools.embed", return_value=[0.1] * 512), \
         patch("agents.factory_rag.tools.pinecone_index", return_value=mock_idx):
        count = upsert_factory_documents([_make_doc()], industry="saas")
        assert count == 0
        mock_idx.upsert.assert_not_called()


def test_upsert_factory_documents_namespace_scoped_to_industry():
    """Namespace must be `delivery_projects__<industry>`."""
    from agents.factory_rag.tools import upsert_factory_documents

    mock_idx = MagicMock()
    mock_idx.query.return_value = MagicMock(matches=[])

    with patch("agents.factory_rag.tools.embed", return_value=[0.1] * 512), \
         patch("agents.factory_rag.tools.pinecone_index", return_value=mock_idx):
        upsert_factory_documents([_make_doc()], industry="real_estate")
        call_kwargs = mock_idx.upsert.call_args.kwargs
        assert call_kwargs["namespace"] == "delivery_projects__real_estate"


def test_prune_factory_documents_returns_int():
    """prune_factory_documents must return an integer count."""
    from agents.factory_rag.tools import prune_factory_documents

    mock_idx = MagicMock()
    mock_idx.delete.return_value = None

    with patch("agents.factory_rag.tools.pinecone_index", return_value=mock_idx):
        result = prune_factory_documents(industry="saas", older_than_days=90)
        assert isinstance(result, int)
