#!/usr/bin/env python3

import gc
from pathlib import Path

import torch

import utils
from segmentor import Segmentor
from vggish import VGGish


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / 'model.pkl'
VGGISH_PATH = ROOT / 'vggish-pretrained.pth'


def main() -> None:
    for path in (MODEL_PATH, VGGISH_PATH):
        if not path.is_file():
            raise FileNotFoundError(f'Missing required model asset: {path}')

    vggish_state = torch.load(VGGISH_PATH, map_location='cpu')
    vggish_model = VGGish(feature_extract=True)
    vggish_model.load_state_dict(vggish_state, strict=True)
    del vggish_state, vggish_model
    gc.collect()

    model = utils.unpickle(str(MODEL_PATH))
    input_size = model.conv_input_size
    audio = torch.zeros(1, 96, 64)
    image = torch.zeros(3, input_size, input_size)
    prediction = Segmentor(model, min_frames=2, threshold=0.5)._predict(audio, image)

    print(f'Model: {type(model).__module__}.{type(model).__name__}')
    print(f'Prediction: {prediction}')
    print('Smoke test: OK')


if __name__ == '__main__':
    main()
