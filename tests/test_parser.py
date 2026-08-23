"""Parser tests for onionmind.py. Run: python tests/test_parser.py

No framework on purpose - this needs to run on a fresh box where pytest isn't
installed, which is exactly the box the installer just built.
"""
import os, re, sys, pathlib
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import onionmind as om

FIXTURE = pathlib.Path(__file__).parent / "ddg-sample.html"
PAGE = FIXTURE.read_text(encoding="utf-8")


def test_parses_real_page():
    hits = om.parse_results(PAGE, n=5)
    assert len(hits) == 5, f"expected 5 results, got {len(hits)}"
    for title, snippet, url in hits:
        assert title, "empty title"
        assert url.startswith("http"), f"bad url: {url}"
        assert "<" not in title and "<" not in snippet, "html leaked into text"


def test_snippets_belong_to_their_own_result():
    """The regression this parser exists for.

    Drop the FIRST result's snippet. Zipping two findall lists by index would
    hand result #1 the snippet that belongs to result #2, and so on down the
    page - silently, with confident-looking citations.
    """
    broken = re.sub(r'<a[^>]*result__snippet.*?</a>', '', PAGE, count=1, flags=re.S)
    hits = om.parse_results(broken, n=5)
    assert hits[0][1] == "", "first result should now have no snippet"

    intact = om.parse_results(PAGE, n=5)
    for i in range(1, len(hits)):
        assert hits[i][1] == intact[i][1], (
            f"result {i} snippet shifted after a missing snippet:\n"
            f"  got:      {hits[i][1][:70]!r}\n"
            f"  expected: {intact[i][1][:70]!r}")


def test_deduplicates_urls():
    hits = om.parse_results(PAGE + PAGE, n=20)
    urls = [u for _, _, u in hits]
    assert len(urls) == len(set(urls)), "duplicate URLs returned"


def test_empty_and_garbage_pages():
    assert om.parse_results("") == []
    assert om.parse_results("<html><body>nothing here</body></html>") == []
    assert om.parse_results('<div class="result">no anchor at all</div>') == []


def test_redirector_urls_are_unwrapped():
    hits = om.parse_results(PAGE, n=10)
    for _, _, url in hits:
        assert "uddg=" not in url, f"DDG redirector left unwrapped: {url}"
        assert "duckduckgo.com/l/" not in url, f"redirector left in place: {url}"


def test_strip_thinking():
    assert om.strip_thinking("<think>hmm</think>The answer") == "The answer"
    assert om.strip_thinking("plain answer") == "plain answer"
    # unterminated <think> means the budget ran out - must NOT return the monologue
    assert om.strip_thinking("<think>still reasoning and never finished") == ""


def test_local_calls_ignore_env_proxies():
    """The privacy invariant: nothing local may ride an exported proxy.

    requests fills a MISSING proxy key from the environment with setdefault, so
    {"http": None, "https": None} alone lets $ALL_PROXY through and every prompt,
    image and answer goes to that proxy. Listing "all" as None is the fix.
    """
    saved = {k: os.environ.get(k) for k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")}
    os.environ["ALL_PROXY"] = "socks5h://127.0.0.1:9"
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
    try:
        merged = requests.Session().merge_environment_settings(
            om.OLLAMA, dict(om.NOPROXY), False, True, None)["proxies"]
        assert not merged, f"local ollama call would be proxied via {dict(merged)}"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL  {name}: {e}")
    print(f"\n{'FAILED' if fails else 'ok'} - {fails} failure(s)")
    sys.exit(1 if fails else 0)
