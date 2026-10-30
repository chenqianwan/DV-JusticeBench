"""Offline policy tests: no cluster, network, model, or existing data writes."""

import copy
import unittest

from scripts.cluster.v4_flash_20261002_autotune.policy import next_action


def observation(concurrency=128, throughput=10, **overrides):
    row = dict(
        concurrency=concurrency, input_sha256="a" * 64, science_sha256="b" * 64,
        elapsed_s=1000 / throughput, attempted=1000, complete=1000,
        valid_format=1000, output_tokens=100000, timeouts=0, ooms=0,
        service_errors=0, preemptions_delta=0, peak_kv_cache=0.2,
        observations_complete=True,
    )
    row.update(overrides)
    return row


class PolicyTests(unittest.TestCase):
    def test_empty_requests_128(self):
        result = next_action([])
        self.assertEqual((result["action"], result["concurrency"]), ("test", 128))

    def test_exponential_ladder_bounded_at_512(self):
        rows = [observation()]
        self.assertEqual(next_action(rows)["concurrency"], 256)
        rows.append(observation(256, 18))
        self.assertEqual(next_action(rows)["concurrency"], 512)
        rows.append(observation(512, 28))
        result = next_action(rows)
        self.assertEqual((result["action"], result["selected_concurrency"]), ("select", 512))

    def test_low_gain_stops_rise_and_uses_near_best_minimum(self):
        result = next_action([observation(), observation(256, 10.5)])
        self.assertEqual(result["action"], "select")
        self.assertEqual(result["selected_concurrency"], 128)

    def test_low_gain_can_select_faster_upgrade_outside_five_percent(self):
        result = next_action([observation(), observation(256, 10.8)])
        self.assertEqual(result["selected_concurrency"], 256)

    def test_ten_percent_gain_is_sufficient(self):
        result = next_action([observation(), observation(256, 11)])
        self.assertEqual(result["concurrency"], 512)

    def test_timeout_upgrade_tries_192_then_selects_stable(self):
        rows = [observation(), observation(256, 18, timeouts=1)]
        self.assertEqual(next_action(rows)["concurrency"], 192)
        rows.append(observation(192, 14))
        self.assertEqual(next_action(rows)["selected_concurrency"], 192)

    def test_failed_midpoint_keeps_128(self):
        rows = [observation(), observation(256, 18, service_errors=1), observation(192, 15, ooms=1)]
        self.assertEqual(next_action(rows)["selected_concurrency"], 128)

    def test_failed_512_tries_384(self):
        rows = [observation(), observation(256, 18), observation(512, 28, preemptions_delta=1)]
        self.assertEqual(next_action(rows)["concurrency"], 384)
        rows.append(observation(384, 18.5))
        self.assertEqual(next_action(rows)["selected_concurrency"], 256)

    def test_baseline_unstable_falls_to_64_then_stops(self):
        rows = [observation(timeouts=1)]
        self.assertEqual(next_action(rows)["concurrency"], 64)
        rows.append(observation(64, 7))
        self.assertEqual(next_action(rows)["selected_concurrency"], 64)

    def test_fallback_32_and_no_stable_block(self):
        rows = [observation(peak_kv_cache=.8), observation(64, 7, ooms=1)]
        self.assertEqual(next_action(rows)["concurrency"], 32)
        rows.append(observation(32, 4, service_errors=1))
        self.assertEqual(next_action(rows)["action"], "blocked")

    def test_kv_ceiling_is_exclusive(self):
        self.assertEqual(next_action([observation(peak_kv_cache=.75)])["concurrency"], 64)
        self.assertEqual(next_action([observation(peak_kv_cache=.749)])["concurrency"], 256)

    def test_incomplete_or_missing_observations_block(self):
        self.assertEqual(next_action([observation(observations_complete=False)])["action"], "blocked")
        row = observation()
        del row["ooms"]
        self.assertEqual(next_action([row])["action"], "blocked")

    def test_different_science_or_workload_block(self):
        for key in ("input_sha256", "science_sha256"):
            with self.subTest(key=key):
                rows = [observation(), observation(256, 18, **{key: "c" * 64})]
                self.assertEqual(next_action(rows)["action"], "blocked")

    def test_format_degradation_and_completion_are_not_throughput_wins(self):
        for override in (dict(valid_format=970), dict(complete=970, valid_format=970)):
            result = next_action([observation(), observation(256, 30, **override)])
            self.assertEqual(result["concurrency"], 192)

    def test_exact_two_point_degradation_permitted(self):
        result = next_action([observation(), observation(256, 18, valid_format=980)])
        self.assertEqual(result["concurrency"], 512)

    def test_zero_successes_never_stable(self):
        result = next_action([observation(complete=0, valid_format=0)])
        self.assertEqual(result["concurrency"], 64)

    def test_output_token_throughput_is_not_primary(self):
        result = next_action([observation(), observation(256, 9, output_tokens=10000000)])
        self.assertEqual(result["selected_concurrency"], 128)

    def test_invalid_numbers_and_counts_block(self):
        invalid = [dict(elapsed_s=0), dict(elapsed_s=float("nan")), dict(peak_kv_cache=75),
                   dict(timeouts=True), dict(complete=1001), dict(valid_format=-1),
                   dict(attempted=0), dict(concurrency=1024), dict(peak_kv_cache=None)]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                self.assertEqual(next_action([observation(**overrides)])["action"], "blocked")

    def test_duplicate_skipped_and_extra_rounds_block(self):
        for rows in ([observation(), observation()],
                     [observation(256)],
                     [observation(), observation(512, 30)],
                     [observation()] * 7,
                     [observation(), observation(256, 10.5), observation(512, 20)]):
            with self.subTest(rows=rows):
                self.assertEqual(next_action(rows)["action"], "blocked")

    def test_no_mutation_and_not_formal_legal_statistics(self):
        rows = [observation(), observation(256, 18)]
        before = copy.deepcopy(rows)
        result = next_action(rows)
        self.assertEqual(rows, before)
        self.assertTrue(result["benchmark_only"])
        self.assertFalse(result["formal_hallucination_statistics_allowed"])


if __name__ == "__main__":
    unittest.main()
