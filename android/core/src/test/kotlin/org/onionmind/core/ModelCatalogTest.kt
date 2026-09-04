package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ModelCatalogTest {
    @Test
    fun `markers are found in names and urls`() {
        assertEquals("abliterated", ModelCatalog.uncensoredMarker("Qwen3.5-9B-abliterated-GGUF"))
        assertEquals(
            "uncensored",
            ModelCatalog.uncensoredMarker(null, "https://example.com/llama-uncensored.gguf"),
        )
    }

    @Test
    fun `absent markers say nothing`() {
        assertNull(ModelCatalog.uncensoredMarker("Qwen3.5-4B", "hf.co/user/model-gguf", null))
    }

    @Test
    fun `the id marker wins over a later text`() {
        assertEquals(
            "abliterated",
            ModelCatalog.uncensoredMarker("model-abliterated", "also-uncensored"),
        )
    }
}
