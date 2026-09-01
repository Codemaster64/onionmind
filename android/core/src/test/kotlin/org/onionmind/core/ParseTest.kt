package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import java.io.File

/**
 * Offline parser tests for the Kotlin port, run against the SAME DuckDuckGo
 * fixture as tests/test_parser.py.
 *
 * The port had drifted twice without anything noticing, because the only
 * Kotlin tests needed a live tor daemon and were excluded by default:
 *   - URLDecoder.decode turned every '+' in a result URL into a space
 *     (it is the FORM decoder; python's unquote leaves '+' alone)
 *   - clean() hand-rolled five entities, missing &#39; and all numeric ones
 */
class ParseTest {

    private val fixtures = File(System.getProperty("onionmind.fixtures") ?: "../../tests")
    private val page: String by lazy { File(fixtures, "ddg-sample.html").readText() }

    @Test fun parsesTheRealPage() {
        val hits = Agent.parseResults(page, 5)
        assertEquals(5, hits.size, "expected 5 results")
        for ((title, snippet, url) in hits) {
            assertTrue(title.isNotEmpty(), "empty title")
            assertTrue(url.startsWith("http"), "bad url: $url")
            assertTrue("<" !in title && "<" !in snippet, "html leaked into text")
            assertTrue("uddg=" !in url, "redirector left unwrapped: $url")
        }
    }

    @Test fun snippetsBelongToTheirOwnResult() {
        val broken = Regex("<a[^>]*result__snippet.*?</a>", RegexOption.DOT_MATCHES_ALL)
            .replaceFirst(page, "")
        val hits = Agent.parseResults(broken, 5)
        val intact = Agent.parseResults(page, 5)
        assertEquals("", hits[0].second, "first result should now have no snippet")
        for (i in 1 until hits.size)
            assertEquals(intact[i].second, hits[i].second, "snippet $i shifted")
    }

    @Test fun deduplicatesUrls() {
        val urls = Agent.parseResults(page + page, 20).map { it.third }
        assertEquals(urls.size, urls.toSet().size, "duplicate URLs returned")
    }

    @Test fun emptyAndGarbagePages() {
        assertTrue(Agent.parseResults("").isEmpty())
        assertTrue(Agent.parseResults("<html><body>nothing here</body></html>").isEmpty())
        assertTrue(Agent.parseResults("""<div class="result">no anchor at all</div>""").isEmpty())
    }

    /** The '+' regression: a form decoder would return ".../a b/". */
    @Test fun plusInUrlSurvives() {
        val p = """<div class="result"><a class="result__a" href="https://x.test/a+b?q=1%2F2">T</a></div>"""
        assertEquals("https://x.test/a+b?q=1/2", Agent.parseResults(p).single().third)
    }

    @Test fun entitiesAndWhitespaceMatchThePythonCleaner() {
        val p = """<div class="result"><a class="result__a" href="https://x.test/">A&amp;B</a>""" +
                """<a class="result__snippet">it&#39;s  a&nbsp;test&#x2014;ok
                   wrapped</a></div>"""
        val (title, snippet, _) = Agent.parseResults(p).single()
        assertEquals("A&B", title)
        assertEquals("it's a test\u2014ok wrapped", snippet)
    }

    @Test fun stripThinking() {
        assertEquals("The answer", Agent.stripThinking("<think>hmm</think>The answer"))
        assertEquals("plain answer", Agent.stripThinking("plain answer"))
        assertEquals("", Agent.stripThinking("<think>still reasoning and never finished"))
    }

    @Test fun stripThinkingVariantsTruncationAndMultipleBlocks() {
        val tagged = "Before < THINK data-mode='private' >FIRST SECRET</ THINK > middle " +
                "<tHiNk\tstage=\"second\">SECOND SECRET</ tHiNk\t> after"
        assertEquals("Before  middle  after", Agent.stripThinking(tagged))
        assertEquals("Visible", Agent.stripThinking("Visible< THI"))
        assertEquals("Visible", Agent.stripThinking("Visible< THINK data-mode='private'"))
        assertEquals("", Agent.stripThinking("< THINK mode=x>SECRET</ THI"))

        val plain = "Math says 2 < 3; <this is ordinary; <thinking> is another tag."
        assertEquals(plain, Agent.stripThinking(plain))
    }
}
