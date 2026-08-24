package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ModelSourceTest {
    @Test fun `reads the filename off a huggingface resolve link`() {
        assertEquals("Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf", ModelSource.filenameFrom(
            "https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF/" +
            "resolve/main/Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf"))
    }

    @Test fun `ignores a query string`() {
        assertEquals("model.gguf",
            ModelSource.filenameFrom("https://example.com/a/model.gguf?download=true&x=1"))
    }

    // The result is joined onto the model directory, so the invariant that
    // matters is not "traversal URLs are rejected" - it is that whatever comes
    // back is a BARE filename. A traversal URL keeps its last segment, which is
    // harmless precisely because that segment cannot contain a separator.
    @Test fun `only ever yields a bare filename`() {
        assertEquals("passwd", ModelSource.filenameFrom("https://example.com/a/../../etc/passwd"))
        for (u in listOf(
            "https://example.com/a/../../etc/passwd",
            "https://example.com/a/%2e%2e%2fpasswd",   // URI decodes this before we split
            "https://example.com/a/..%2f..%2fb.gguf",
            "https://example.com/../x.gguf",
            "https://example.com/a b/c d.gguf",
        )) {
            val f = ModelSource.filenameFrom(u)
            assertTrue(f == null || (!f.contains('/') && !f.contains('\\') && f != ".." && f != "."),
                "leaked a path for $u: $f")
        }
    }

    @Test fun `returns null when there is no usable segment`() {
        assertNull(ModelSource.filenameFrom("https://example.com/a/"))
        assertNull(ModelSource.filenameFrom("https://example.com"))
        assertNull(ModelSource.filenameFrom("not a url at all"))
        assertNull(ModelSource.filenameFrom("https://example.com/a/%2e%2e%2f"))
    }

    @Test fun `strips the weights extension for a label`() {
        assertEquals("gemma-3-4b-it-Q4_K_M", ModelSource.nameFrom("gemma-3-4b-it-Q4_K_M.gguf"))
        assertEquals("weights.bin", ModelSource.nameFrom("weights.bin"))
    }
}
