"""Tests for CLI interactive module — sensitive data masking, tool formatting."""
import pytest
import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tokio_cli.interactive import _mask_sensitive, _format_tool_start, TOOL_ICONS


class TestSensitiveMasking:
    """Test that sensitive data is properly masked in CLI output."""

    def test_ipv4_masked(self):
        assert "[IP]" in _mask_sensitive("Server at 192.168.1.100")

    def test_github_pat_masked(self):
        text = "token: github_pat_11FAKEFAKE0FAKE000000_FakeTokenForTestingOnly00000000000000000000000000000000000"
        result = _mask_sensitive(text)
        assert "github_pat_" not in result
        assert "[GITHUB_TOKEN]" in result

    def test_github_ghp_masked(self):
        text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl"
        result = _mask_sensitive(text)
        assert "ghp_" not in result

    def test_openai_key_masked(self):
        text = "OPENAI_API_KEY=sk-proj-12345678901234567890"
        result = _mask_sensitive(text)
        assert "sk-" not in result

    def test_aws_key_masked(self):
        text = "key: AKIAIOSFODNN7EXAMPLE"
        result = _mask_sensitive(text)
        assert "AKIA" not in result

    def test_ssh_key_masked(self):
        text = "ssh -i /home/user/.ssh/id_rsa root@server"
        result = _mask_sensitive(text)
        assert "id_rsa" not in result

    def test_password_masked(self):
        text = 'password: "my_secret_pass"'
        result = _mask_sensitive(text)
        assert "my_secret_pass" not in result

    def test_anthropic_key_masked(self):
        text = "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890"
        result = _mask_sensitive(text)
        assert "sk-ant" not in result

    def test_normal_text_untouched(self):
        text = "All systems operational. 97 tests passed."
        assert _mask_sensitive(text) == text

    def test_telegram_token_masked(self):
        text = "TELEGRAM_BOT_TOKEN=123456:ABCdefGHIjklMNO"
        result = _mask_sensitive(text)
        assert "123456" not in result


class TestToolIcons:
    """Test that all tools have icons."""

    def test_core_tools_have_icons(self):
        core = ["bash", "python", "read_file", "write_file", "edit_file",
                "search_code", "find_files", "list_files"]
        for name in core:
            assert name in TOOL_ICONS, f"Missing icon for core tool: {name}"

    def test_infra_tools_have_icons(self):
        infra = ["docker", "raspi_vision", "postgres_query", "gcp_waf",
                 "host_control", "router_control", "infra"]
        for name in infra:
            assert name in TOOL_ICONS, f"Missing icon for infra tool: {name}"

    def test_device_tools_have_icons(self):
        devices = ["coffee", "drone", "iot_control"]
        for name in devices:
            assert name in TOOL_ICONS, f"Missing icon for device tool: {name}"

    def test_security_tools_have_icons(self):
        sec = ["security", "prompt_guard", "self_heal"]
        for name in sec:
            assert name in TOOL_ICONS, f"Missing icon for security tool: {name}"


class TestToolFormatting:
    """Test tool start formatting."""

    def test_bash_shows_command(self):
        result = _format_tool_start("bash", {"command": "ls -la"}, 1)
        assert "ls -la" in result
        assert "bash" in result

    def test_action_tool_shows_params(self):
        result = _format_tool_start("raspi_vision", {
            "action": "see",
            "params": {"text": "hello"}
        }, 1)
        assert "see" in result

    def test_python_shows_line_count(self):
        result = _format_tool_start("python", {"code": "a = 1\nb = 2\nprint(a+b)"}, 1)
        assert "3 lines" in result

    def test_query_shows_sql(self):
        result = _format_tool_start("postgres_query", {"query": "SELECT * FROM logs"}, 1)
        assert "SELECT" in result

    def test_url_shown(self):
        result = _format_tool_start("curl", {"url": "https://api.example.com/test"}, 1)
        assert "example.com" in result

    def test_unknown_tool_gets_default_icon(self):
        result = _format_tool_start("new_unknown_tool", {}, 1)
        assert "new_unknown_tool" in result

    def test_sensitive_data_masked_in_commands(self):
        result = _format_tool_start("bash", {
            "command": "ssh -i /root/.ssh/key user@192.168.1.1"
        }, 1)
        assert "192.168" not in result
        assert "[IP]" in result


class TestMarkdownRenderer:
    """Test markdown rendering for terminal."""

    def test_bold_rendering(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("This is **bold** text")
        assert "bold" in result
        assert "**" not in result

    def test_inline_code(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("Run `ls -la` now")
        assert "ls -la" in result
        assert "`" not in result.replace("\033[", "")  # ignore ANSI

    def test_header_h1(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("# Big Title")
        assert "Big Title" in result

    def test_header_h2(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("## Section")
        assert "Section" in result

    def test_header_h3(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("### Subsection")
        assert "Subsection" in result

    def test_unordered_list(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("- Item one\n- Item two")
        assert "Item one" in result
        assert "Item two" in result

    def test_ordered_list(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("1. First\n2. Second")
        assert "First" in result
        assert "Second" in result

    def test_code_block(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("```python\nprint('hello')\n```")
        assert "print" in result
        assert "hello" in result

    def test_checkbox(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("- [x] Done\n- [ ] Todo")
        assert "Done" in result
        assert "Todo" in result

    def test_blockquote(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("> This is a quote")
        assert "This is a quote" in result

    def test_table(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("| Name | Value |\n|------|-------|\n| foo  | bar   |")
        assert "foo" in result
        assert "bar" in result

    def test_link(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("[TokioAI](https://github.com/TokioAI)")
        assert "TokioAI" in result

    def test_horizontal_rule(self):
        from tokio_cli.interactive import MarkdownRenderer
        result = MarkdownRenderer.render("---")
        assert "─" in result

    def test_tool_result_format(self):
        from tokio_cli.interactive import _format_tool_result
        ok = _format_tool_result("bash", "file created", True)
        assert "✓" in ok
        fail = _format_tool_result("bash", "error: not found", False)
        assert "✗" in fail

    def test_empty_tool_result(self):
        from tokio_cli.interactive import _format_tool_result
        result = _format_tool_result("bash", "", True)
        assert "done" in result
