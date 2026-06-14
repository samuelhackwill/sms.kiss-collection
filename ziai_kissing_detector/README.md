# Kissing Detector
Detect kissing scenes in a movie using both audio and video features.

Project for [Stanford CS231N](http://cs231n.stanford.edu)

## Resources
- [Paper](https://arxiv.org/abs/1906.01843)
- [Poster](poster.pdf)
- [Video](https://www.youtube.com/watch?v=3IIGLupGAkI)

## Setup
The current dependency pins are verified with Python 3.12. They do not install
under the system Python 3.13 or newer because `torch==2.2.2` does not provide
wheels for those Python versions.

FFmpeg must also be available on `PATH` for video and audio extraction.

```bash
cd ziai_kissing_detector
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`model.pkl` is required to use the bundled detector.
`vggish-pretrained.pth` is a local weights cache; when it is absent, new
VGGish model creation falls back to downloading the weights. Both files are
intentionally gitignored because of their size. Download `model.pkl` from the
link in the detection example below if it is missing.

Verify the setup and both local model assets:

```bash
python -m pip check
python -m unittest discover -v
python smoke_test.py
```

## Running the code
```bash
python3 experiments.py
```

this will run the experiments in `params.py` specified by the `experiments` dictionary.

## Requirements
This is a PyTorch project. Look at `requirements.txt` for more details. 

## Build dataset
The following will build the dataset for training. You need to provide path to video segments.
```python
from pipeline import BuildDataset

videos_and_labels = [
    # (file name in base_path, label) where label is 1 for kissing and 0 for not kissing
    ('movies_casino_royale_2006_kissing_1.mp4', 1),
    ('movies_casino_royale_2006_kissing_2.mp4', 1),
    ('movies_casino_royale_2006_kissing_3.mp4', 1),
    ('movies_casino_royale_2006_not_1.mp4', 0),
    ('movies_casino_royale_2006_not_2.mp4', 0),
    ('movies_casino_royale_2006_not_3.mp4', 0),
    
    ('movies_goldeneye_1995_kissing_1.mp4', 1),
    ('movies_goldeneye_1995_kissing_2.mp4', 1),
    ('movies_goldeneye_1995_kissing_3.mp4', 1),
    ('movies_goldeneye_1995_not_1.mp4', 0),
    ('movies_goldeneye_1995_not_2.mp4', 0),
    ('movies_goldeneye_1995_not_3.mp4', 0),
]

builder = BuildDataset(base_path='path/to/movies',
                 videos_and_labels=videos_and_labels,
                 output_path='/path/to/output',
                 test_size=1 / 3)  # set aside 1 / 3 of data for validation
builder.build_dataset()
```

## Detect kissing segments in a given video
```python
from segmentor import Segmentor
import utils

# download model.pkl from https://drive.google.com/file/d/1RlvvdInTXtJikGv_ZbHcKoblCypN1Z0A/view?usp=sharing
# or train your own
model = utils.unpickle('model.pkl')  # pickled PyTorch model 
s = Segmentor(model, min_frames=10, threshold=0.7)

# For YouTube clip Hot Summer Nights - Kiss Scene (Maika Monroe and Timothee Chalamet)
# at https://www.youtube.com/watch?v=GG5HmLQ_Fx0
# v=XXX is the YouTube ID, pass that here 
s.visualize_segments_youtube('GG5HmLQ_Fx0')

# alternatively you can provide a path to a local mp4 file
s.visualize_segments('path/to/file.mp4')
```

See examples in [examples/detector.ipynb](examples/detector.ipynb).

## References
- [Video Classification Using 3D ResNet](https://github.com/kenshohara/video-classification-3d-cnn-pytorch)
- [3D ResNets for Action Recognition (CVPR 2018)](https://github.com/kenshohara/3D-ResNets-PyTorch/)
- [AudioSet](https://research.google.com/audioset/download.html)
- [TensorFlow AudioSet](https://github.com/tensorflow/models/tree/master/research/audioset)
- [CS231N Saliency maps and class viz PyTorch code](http://cs231n.github.io/assignments2019/assignment3/)
- [Torch VGGish](https://github.com/harritaylor/torchvggish)
