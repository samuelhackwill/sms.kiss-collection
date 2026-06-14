import unittest

from segmentor import Segmentor


class TestSegmentor(unittest.TestCase):
    def test_segmentor(self):
        tests = [
            ([1, 1, 0, 1, 0, 0, 1, 0, 1], [[0, 1, 2, 3]]),
            ([1, 1, 0, 1, 1, 0, 1, 0, 1], [[0, 1, 2, 3, 4, 5, 6]]),
            ([1, 1, 0, 1, 1, 0, 1, 1, 1], [list(range(0, 8 + 1))]),
            ([1, 1, 1, 0, 1], [[0, 1, 2, 3, 4]]),
            ([0, 0, 0, 0, 0], []),
            ([1] * 7 + [0] * 3, [list(range(7))])
        ]

        min_frames = 4
        threshold = 0.7

        for ex, exp in tests:
            self.assertEqual(exp, Segmentor._segmentor(ex, min_frames, threshold))

    def test_segmentor_keeps_longer_overlapping_candidate(self):
        preds = [1, 0, 1, 0, 0, 1, 1]

        self.assertEqual(
            [list(range(2, 7))],
            Segmentor._segmentor(preds, min_frames=3, threshold=0.6)
        )
