import random
import unittest

from tests import util  # noqa: F401
from omatrade.sse import MAX_EVENT_BYTES, Backoff, SSEParser


def feed_all(parser, text):
    out = []
    for line in text.splitlines(keepends=True):
        ev = parser.feed(line)
        if ev:
            out.append(ev)
    return out


class Parser(unittest.TestCase):
    def test_basic_event(self):
        p = SSEParser()
        evs = feed_all(p, "id: 7\nevent: signal\ndata: {\"a\":1}\n\n")
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0].event, evs[0].data, evs[0].id), ("signal", '{"a":1}', "7"))

    def test_multiline_data_comments_and_default_event(self):
        p = SSEParser()
        evs = feed_all(p, ": heartbeat\ndata: one\ndata:two\ndata:  three\n\n")
        self.assertEqual(evs[0].event, "message")
        self.assertEqual(evs[0].data, "one\ntwo\n three")

    def test_crlf_bytes_and_bom(self):
        p = SSEParser()
        out = [p.feed(b"\xef\xbb\xbfdata: x\r\n"), p.feed(b"\r\n")]
        self.assertEqual(out[1].data, "x")

    def test_split_utf8_is_replaced_not_crashing(self):
        p = SSEParser()
        p.feed(b"data: \xe2\x82\n")
        self.assertIsNotNone(p.feed(b"\n"))

    def test_id_persists_and_nul_ignored(self):
        p = SSEParser()
        feed_all(p, "id: 1\ndata: a\n\n")
        ev = feed_all(p, "data: b\n\n")[0]
        self.assertEqual(ev.id, "1")
        feed_all(p, "id: bad\x00id\ndata: c\n\n")
        self.assertEqual(p.last_event_id, "1")

    def test_retry_must_be_digits(self):
        p = SSEParser()
        feed_all(p, "retry: 1500\n\nretry: soon\n\n")
        self.assertEqual(p.retry, 1500)

    def test_blank_without_data_does_not_dispatch(self):
        p = SSEParser()
        self.assertEqual(feed_all(p, "event: x\n\n\n"), [])
        # the event name does not leak into the next event
        self.assertEqual(feed_all(p, "data: y\n\n")[0].event, "message")

    def test_oversized_event_is_dropped(self):
        p = SSEParser()
        big = "x" * 1000
        for _ in range(MAX_EVENT_BYTES // 1000 + 5):
            p.feed("data: " + big)
        self.assertIsNone(p.feed(""))
        self.assertEqual(feed_all(p, "data: ok\n\n")[0].data, "ok")

    def test_field_without_colon(self):
        p = SSEParser()
        p.feed("data")
        ev = p.feed("")
        self.assertEqual(ev.data, "")


class BackoffTest(unittest.TestCase):
    def test_bounds_grow_and_cap(self):
        b = Backoff(base=1, cap=30, rng=random.Random(1))
        ceilings = []
        for n in range(10):
            d = b.next()
            ceiling = min(30, 2 ** n)
            self.assertGreaterEqual(d, 0)
            self.assertLessEqual(d, ceiling)
            ceilings.append(ceiling)
        self.assertEqual(ceilings[-1], 30)

    def test_full_jitter_spreads_clients(self):
        delays = []
        for seed in range(200):
            b = Backoff(base=1, cap=300, rng=random.Random(seed))
            for _ in range(5):
                d = b.next()
            delays.append(d)
        # five failures: uniform in [0, 16]; a thundering herd would be one value
        self.assertGreater(len({round(d, 3) for d in delays}), 150)
        self.assertLess(min(delays), 4)
        self.assertGreater(max(delays), 12)
        self.assertTrue(all(0 <= d <= 16 for d in delays))

    def test_minimum_from_server_and_reset(self):
        b = Backoff(base=1, cap=60, rng=random.Random(0))
        self.assertGreaterEqual(b.next(minimum=20), 20)
        self.assertLessEqual(b.next(minimum=10_000), 60)  # never beyond the cap
        b.reset()
        self.assertEqual(b.attempt, 0)
        self.assertLessEqual(b.next(), 1)


if __name__ == "__main__":
    unittest.main()
