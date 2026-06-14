import os
import pickle
import shutil
import tempfile
from typing import Callable, List, Sequence, Tuple

import cv2
import numpy as np
import torch
from PIL import Image
from moviepy.editor import VideoFileClip

from torchvision import transforms

import params
import vggish_input


class BuildDataset:
    def __init__(self,
                 base_path: str,
                 videos_and_labels: List[Tuple[str, str]],
                 output_path: str,
                 n_augment: int = 1,
                 test_size: float = 1 / 3):
        assert 0 < test_size < 1
        self.videos_and_labels = videos_and_labels
        self.test_size = test_size
        self.output_path = output_path
        self.base_path = base_path
        self.n_augment = n_augment

        self.sets = ['train', 'val']

    def _get_set(self):
        return np.random.choice(self.sets, p=[1 - self.test_size, self.test_size])

    def build_dataset(self):
        # wipe
        for set_ in self.sets:
            path = f'{self.output_path}/{set_}'
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            os.makedirs(path)

        for file_name, label in self.videos_and_labels:
            name, _ = file_name.split('.')
            path = f'{self.base_path}/{file_name}'
            audio, images = self.one_video_extract_audio_and_stills(path, training=True)
            set_ = self._get_set()
            target = f"{self.output_path}/{set_}/{label}_{name}.pkl"
            pickle.dump((audio, images, label), open(target, 'wb'))

    @staticmethod
    def transform_reverse(img: torch.Tensor) -> Image:
        return transforms.Compose([
            transforms.Normalize(mean=[0, 0, 0], std=(1.0 / params.std).tolist()),
            transforms.Normalize(mean=(-params.mean).tolist(), std=[1, 1, 1]),
            transforms.ToPILImage()])(img)

    @staticmethod
    def training_transformer(img_size: int):
        return transforms.Compose([
            transforms.RandomResizedCrop(img_size),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize(params.mean, params.std)
        ])

    @staticmethod
    def inference_transformer(img_size: int):
        # The paper uses a random crop at test time, but no horizontal flip.
        return transforms.Compose([
            transforms.RandomResizedCrop(img_size),
            transforms.ToTensor(),
            transforms.Normalize(params.mean, params.std)
        ])

    @staticmethod
    def transformer(img_size: int):
        return BuildDataset.training_transformer(img_size)

    @staticmethod
    def _target_frame_indices(frame_timestamps: Sequence[float],
                              target_timestamps: Sequence[float]) -> List[int]:
        """Return the final frame at or before each target timestamp."""
        frame_indices = []
        frame_idx = 0

        for target in target_timestamps:
            while (frame_idx + 1 < len(frame_timestamps)
                   and frame_timestamps[frame_idx + 1] <= target):
                frame_idx += 1
            if frame_timestamps and frame_timestamps[frame_idx] <= target:
                frame_indices.append(frame_idx)

        return frame_indices

    @staticmethod
    def _frame_to_image(frame: np.ndarray) -> Image:
        # Preserve the channel order used when the official model was trained.
        return Image.fromarray(frame)

    @classmethod
    def _extract_stills_at_timestamps(cls,
                                      path_video: str,
                                      target_timestamps: Sequence[float],
                                      transformer: Callable[[Image], torch.Tensor]
                                      ) -> List[torch.Tensor]:
        cap = cv2.VideoCapture(path_video)
        images = []
        target_idx = 0
        previous_frame = None
        previous_timestamp = None
        frame_rate = cap.get(cv2.CAP_PROP_FPS)
        frame_interval = 1.0 / frame_rate if frame_rate > 0 else 0.0

        while cap.isOpened() and target_idx < len(target_timestamps):
            success, frame = cap.read()
            if not success:
                break

            timestamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            while target_idx < len(target_timestamps) and timestamp > target_timestamps[target_idx]:
                if previous_frame is not None:
                    images.append(transformer(cls._frame_to_image(previous_frame)))
                target_idx += 1

            previous_frame = frame
            previous_timestamp = timestamp

        cap.release()

        if (target_idx < len(target_timestamps)
                and previous_frame is not None
                and previous_timestamp <= target_timestamps[target_idx] <= previous_timestamp + frame_interval):
            images.append(transformer(cls._frame_to_image(previous_frame)))

        return images

    @staticmethod
    def _extract_audio(path_video: str) -> np.ndarray:
        os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_audio_file = tmp.name

        clip = VideoFileClip(path_video)
        try:
            clip.audio.write_audiofile(tmp_audio_file, logger=None)
            return vggish_input.wavfile_to_examples(tmp_audio_file)
        finally:
            clip.close()
            os.remove(tmp_audio_file)

    @classmethod
    def one_video_extract_audio_and_stills(cls,
                                           path_video: str,
                                           img_size: int = 224,
                                           training: bool = False
                                           ) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
        audio_examples = cls._extract_audio(path_video)
        target_timestamps = [
            (idx + 1) * params.vggish_frame_rate
            for idx in range(len(audio_examples))
        ]
        transformer = (cls.training_transformer(img_size)
                       if training else cls.inference_transformer(img_size))
        images = cls._extract_stills_at_timestamps(path_video, target_timestamps, transformer)

        min_sizes = min(audio_examples.shape[0], len(images))
        audio = [
            torch.from_numpy(audio_examples[idx][None, :, :]).float()
            for idx in range(min_sizes)
        ]
        images = images[:min_sizes]

        return audio, images
