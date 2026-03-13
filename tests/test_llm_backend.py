"""
Test cases for llm_backend module functions.
"""

import pytest

from wichy.llm_backend import parse_generic_backend


class TestParseGenericBackend:
    """Tests for parse_generic_backend function."""

    # Basic parsing tests
    def test_localhost_with_port(self):
        result = parse_generic_backend("generic/localhost:8080##llama-3")
        assert result == ("http://localhost:8080/v1", "llama-3")

    def test_localhost_no_port(self):
        result = parse_generic_backend("generic/localhost##model-name")
        assert result == ("http://localhost/v1", "model-name")

    def test_remote_host_https(self):
        result = parse_generic_backend("generic/api.myservice.com##gpt-4")
        assert result == ("https://api.myservice.com/v1", "gpt-4")

    def test_remote_host_with_port(self):
        result = parse_generic_backend("generic/api.example.com:9000##my-model")
        assert result == ("https://api.example.com:9000/v1", "my-model")

    # Local/private IP tests (should use http)
    def test_127_loopback(self):
        result = parse_generic_backend("generic/127.0.0.1:8080##test-model")
        assert result == ("http://127.0.0.1:8080/v1", "test-model")

    def test_192_168_private_ip(self):
        result = parse_generic_backend("generic/192.168.1.10:9000##my-model")
        assert result == ("http://192.168.1.10:9000/v1", "my-model")

    def test_10_private_ip(self):
        result = parse_generic_backend("generic/10.0.0.5##internal-model")
        assert result == ("http://10.0.0.5/v1", "internal-model")

    def test_172_private_ip(self):
        result = parse_generic_backend("generic/172.16.0.1##private-model")
        assert result == ("http://172.16.0.1/v1", "private-model")

    # Model name variations
    def test_model_with_slashes(self):
        result = parse_generic_backend("generic/api.openai.com##org/model-name")
        assert result == ("https://api.openai.com/v1", "org/model-name")

    def test_model_with_multiple_slashes(self):
        result = parse_generic_backend("generic/api.provider.com##org/team/model-v2")
        assert result == ("https://api.provider.com/v1", "org/team/model-v2")

    def test_model_with_dashes_and_underscores(self):
        result = parse_generic_backend("generic/myserver.com##my_awesome-model_v2")
        assert result == ("https://myserver.com/v1", "my_awesome-model_v2")

    def test_model_with_colon_version(self):
        result = parse_generic_backend("generic/localhost:8080##llama-3:8b")
        assert result == ("http://localhost:8080/v1", "llama-3:8b")

    # Whitespace handling
    def test_leading_trailing_whitespace(self):
        result = parse_generic_backend("  generic/localhost:8080##model  ")
        assert result == ("http://localhost:8080/v1", "model")

    def test_whitespace_around_model(self):
        result = parse_generic_backend("generic/localhost:8080##  model-name  ")
        assert result == ("http://localhost:8080/v1", "model-name")

    # Error cases
    def test_missing_double_hash(self):
        with pytest.raises(ValueError, match="Expected 'generic/<host>##<model>'"):
            parse_generic_backend("generic/localhost:8080/model")

    def test_empty_host(self):
        with pytest.raises(ValueError, match="Host is empty"):
            parse_generic_backend("generic/##model")

    def test_empty_model(self):
        with pytest.raises(ValueError, match="Model is empty"):
            parse_generic_backend("generic/localhost:8080##")

    def test_missing_backend_prefix(self):
        with pytest.raises(ValueError, match="Invalid generic backend format"):
            parse_generic_backend("localhost:8080##model")

    def test_only_backend_prefix(self):
        with pytest.raises(ValueError, match="Invalid generic backend format"):
            parse_generic_backend("generic/")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid generic backend format"):
            parse_generic_backend("")