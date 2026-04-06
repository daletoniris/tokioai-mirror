"""Tests for Error Learner — adaptive error recovery."""
import pytest
from tokio_agent.engine.error_learner import ErrorLearner, KNOWN_PATTERNS


class TestErrorLearner:
    def setup_method(self):
        self.learner = ErrorLearner()

    def test_analyze_known_error(self):
        """Known error patterns should return a fix hint."""
        result = self.learner.analyze_error("bash", "bash: jq: command not found")
        assert result is not None
        assert "instal" in result.lower()  # instalarlo / install

    def test_analyze_permission_denied(self):
        result = self.learner.analyze_error("bash", "Permission denied: /etc/shadow")
        assert result is not None
        assert "sudo" in result.lower() or "permisos" in result.lower()

    def test_analyze_connection_refused(self):
        result = self.learner.analyze_error("curl", "Connection refused on port 8080")
        assert result is not None
        assert "servicio" in result.lower() or "puerto" in result.lower()

    def test_analyze_file_not_found(self):
        result = self.learner.analyze_error("read_file", "No such file or directory: /tmp/x")
        assert result is not None
        assert "ruta" in result.lower() or "existe" in result.lower()

    def test_analyze_module_not_found(self):
        result = self.learner.analyze_error("python", "ModuleNotFoundError: No module named 'pandas'")
        assert result is not None
        assert "pip" in result.lower() or "install" in result.lower()

    def test_analyze_timeout(self):
        result = self.learner.analyze_error("bash", "Operation timeout after 60s")
        assert result is not None
        assert "timeout" in result.lower() or "tiempo" in result.lower()

    def test_unknown_error_still_returns_suggestion(self):
        result = self.learner.analyze_error("bash", "some random unknown error xyz")
        assert result is not None
        assert "diferente" in result.lower() or "enfoque" in result.lower()

    def test_max_retries_returns_none(self):
        """After MAX_RETRIES_PER_ERROR, analyze should return None (bail out)."""
        for i in range(ErrorLearner.MAX_RETRIES_PER_ERROR):
            result = self.learner.analyze_error("bash", "command not found: xyz")
            assert result is not None, f"Should not bail on attempt {i+1}"
        # Now should bail
        result = self.learner.analyze_error("bash", "command not found: xyz")
        assert result is None

    def test_should_retry(self):
        assert self.learner.should_retry("bash", "command not found")
        for _ in range(ErrorLearner.MAX_RETRIES_PER_ERROR):
            self.learner.analyze_error("bash", "command not found: abc")
        assert not self.learner.should_retry("bash", "command not found")

    def test_reset_tool(self):
        """After success, retry counts should reset."""
        for _ in range(2):
            self.learner.analyze_error("bash", "command not found: xyz")
        self.learner.reset_tool("bash")
        # Should be able to retry again
        assert self.learner.should_retry("bash", "command not found")

    def test_different_errors_have_separate_budgets(self):
        """Different error types should have independent retry counts."""
        for _ in range(ErrorLearner.MAX_RETRIES_PER_ERROR):
            self.learner.analyze_error("bash", "command not found: abc")
        # 'command not found' exhausted, but 'permission denied' is fresh
        result = self.learner.analyze_error("bash", "Permission denied")
        assert result is not None

    def test_context_for_prompt(self):
        """Error context should be generated for the LLM."""
        self.learner.analyze_error("bash", "command not found: jq")
        ctx = self.learner.get_context_for_prompt()
        assert "bash" in ctx
        assert "Error" in ctx or "error" in ctx

    def test_empty_context_when_no_errors(self):
        ctx = self.learner.get_context_for_prompt()
        assert ctx == ""


class TestKnownPatterns:
    def test_patterns_have_required_fields(self):
        """All patterns should have pattern and fix_hint."""
        for p in KNOWN_PATTERNS:
            assert p.pattern, f"Empty pattern: {p}"
            assert p.fix_hint, f"Empty fix_hint for: {p.pattern}"

    def test_no_duplicate_patterns(self):
        patterns = [p.pattern for p in KNOWN_PATTERNS]
        assert len(patterns) == len(set(patterns)), "Duplicate patterns found"

    def test_minimum_patterns_count(self):
        """Should have at least 10 known patterns."""
        assert len(KNOWN_PATTERNS) >= 10
