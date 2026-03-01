"""Tests for Document Tools — PDF, Slides, CSV generation."""
import json
import os
import tempfile
import pytest

from tokio_agent.engine.tools.builtin.document_tools import (
    _generate_pdf,
    _generate_slides,
    _generate_csv,
    document_tool,
)


class TestGeneratePDF:
    def test_basic_pdf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "test.pdf")
            result = _generate_pdf(
                title="Test Report",
                sections=[
                    {"heading": "Section 1", "body": "This is the body text."},
                    {"heading": "Section 2", "body": "More content here."},
                ],
                output_path=output,
            )
            data = json.loads(result)
            assert data["ok"] is True
            assert os.path.exists(output)
            assert data["size_bytes"] > 0
            assert data["pages"] >= 1

    def test_empty_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "empty.pdf")
            result = _generate_pdf("Empty", [], output_path=output)
            data = json.loads(result)
            assert data["ok"] is True

    def test_security_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "security.pdf")
            result = _generate_pdf(
                title="Security Report",
                sections=[{"heading": "Findings", "body": "No issues found."}],
                output_path=output,
                template="security",
            )
            data = json.loads(result)
            assert data["ok"] is True


class TestGenerateCSV:
    def test_basic_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "test.csv")
            result = _generate_csv(
                data=[["1.2.3.4", "100", "blocked"], ["5.6.7.8", "50", "allowed"]],
                headers=["IP", "Hits", "Status"],
                output_path=output,
            )
            data = json.loads(result)
            assert data["ok"] is True
            assert data["rows"] == 2
            assert os.path.exists(output)

            # Verify content
            with open(output) as f:
                content = f.read()
            assert "IP,Hits,Status" in content
            assert "1.2.3.4" in content

    def test_empty_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "empty.csv")
            result = _generate_csv([], output_path=output)
            data = json.loads(result)
            assert data["ok"] is True
            assert data["rows"] == 0


class TestGenerateSlides:
    def test_basic_slides(self):
        pytest.importorskip("pptx")
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "test.pptx")
            result = _generate_slides(
                title="Test Presentation",
                slides=[
                    {"title": "Slide 1", "bullets": ["Point A", "Point B"]},
                    {"title": "Slide 2", "content": "Some text content"},
                ],
                output_path=output,
            )
            data = json.loads(result)
            assert data["ok"] is True
            assert data["slides_count"] == 3  # title + 2 content
            assert os.path.exists(output)


class TestDocumentTool:
    @pytest.mark.asyncio
    async def test_pdf_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await document_tool("generate_pdf", {
                "title": "Async Test",
                "sections": [{"heading": "H1", "body": "Body"}],
                "output_path": os.path.join(tmpdir, "async.pdf"),
            })
            data = json.loads(result)
            assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_csv_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await document_tool("generate_csv", {
                "data": [["a", "b"], ["c", "d"]],
                "output_path": os.path.join(tmpdir, "async.csv"),
            })
            data = json.loads(result)
            assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_invalid_action(self):
        result = await document_tool("invalid")
        data = json.loads(result)
        assert data["ok"] is False
        assert "supported" in data
