from heguang_code.features import technical, words


def test_github_source_url_and_diff_paths_are_normalized():
    query_paths, query_symbols = technical("See https://github.com/team/repo/blob/abc123/src/parser.py#L42 and `parsePacket`.")
    source_paths, _ = technical("diff --git a/src/parser.py b/src/parser.py\n+def parsePacket(value):")
    assert "src/parser.py" in query_paths & source_paths
    assert "github.com" not in query_symbols
    assert "parsepacket" in query_symbols


def test_identifier_components_keep_exact_and_abbreviation_information():
    tokens = words("HTTPResponseParser cache_entry 请求处理")
    assert {"httpresponseparser", "http", "response", "parser", "cache_entry", "cache", "entry", "请求", "处理"} <= set(tokens)
