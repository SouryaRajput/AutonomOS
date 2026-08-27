import time
import unittest

from core.inference.gateway import CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):

    def test_circuit_breaker_threshold_and_cooldown_lifecycle(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.1)
        provider_id = "provider-flake"

        self.assertFalse(cb.is_open(provider_id))

        # First failure -> still CLOSED
        cb.record_failure(provider_id)
        self.assertFalse(cb.is_open(provider_id))

        # Second failure -> reaches threshold -> OPEN
        cb.record_failure(provider_id)
        self.assertTrue(cb.is_open(provider_id))

        # Wait for cooldown
        time.sleep(0.15)

        # After cooldown -> transitions to HALF-OPEN (is_open returns False to allow test request)
        self.assertFalse(cb.is_open(provider_id))

        # Successful test request -> resets breaker to CLOSED
        cb.record_success(provider_id)
        self.assertFalse(cb.is_open(provider_id))


if __name__ == "__main__":
    unittest.main()
