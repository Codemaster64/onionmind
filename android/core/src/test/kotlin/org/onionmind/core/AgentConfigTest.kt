package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertTrue

class AgentConfigTest {

    @Test fun reasoningBudgetExceedsTheExhaustedCeiling() {
        assertTrue(
            Agent.NUM_PREDICT > 8192,
            "reasoning models can spend the old 8192-token budget before answering",
        )
    }
}
