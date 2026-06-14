import unittest

import numpy as np
from torchvision import transforms

from pipeline import BuildDataset


class TestPipeline(unittest.TestCase):
    def test_inference_transform_does_not_flip(self):
        transform = BuildDataset.inference_transformer(224)

        self.assertFalse(any(
            isinstance(item, transforms.RandomHorizontalFlip)
            for item in transform.transforms
        ))

    def test_training_transform_flips(self):
        transform = BuildDataset.training_transformer(224)

        self.assertTrue(any(
            isinstance(item, transforms.RandomHorizontalFlip)
            for item in transform.transforms
        ))

    def test_target_frame_indices_select_last_frame_before_endpoint(self):
        frame_timestamps = [0.0, 0.4, 0.9, 1.1, 1.8, 2.0]
        target_timestamps = [0.96, 1.92]

        self.assertEqual(
            [2, 4],
            BuildDataset._target_frame_indices(frame_timestamps, target_timestamps)
        )

    def test_frame_to_image_preserves_official_model_channel_order(self):
        bgr_pixel = np.array([[[255, 0, 0]]], dtype=np.uint8)

        image = BuildDataset._frame_to_image(bgr_pixel)

        self.assertEqual((255, 0, 0), image.getpixel((0, 0)))


if __name__ == '__main__':
    unittest.main()
