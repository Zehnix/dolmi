# Third-party notices

Dolmi bundles the components below. Each is used under its own licence. The Windows installer and
the macOS app bundle slightly different sets: the Windows list comes first, the [macOS list](#macos-dolmiapp)
after it.

# Code adapted from other projects (both builds)

- **whisper_streaming** (`streaming.py`: `HypothesisBuffer` and the update/trim logic of `StableDraft`,
  ported from `whisper_online.py` `HypothesisBuffer` / `OnlineASRProcessor`), MIT License,
  Copyright (c) 2023 ÚFAL — https://github.com/ufal/whisper_streaming

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction,
  including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
  and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
  subject to the following conditions: The above copyright notice and this permission notice shall be
  included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
  WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
  MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
  COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
  TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
  IN THE SOFTWARE.

# Windows (Dolmi-Setup.exe)

The full licence texts are inside each package's folder in `runtime\Lib\site-packages\<package>-<version>.dist-info`.

## Runtime

- **Python 3.12** (embeddable distribution), Python Software Foundation License — https://www.python.org/psf/license/

## Python packages

| Package | Version | Licence | Project |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anthropic | 1.11.0 | MIT License | https://github.com/anthropics/anthropic-sdk-python |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| av | 19.0.0 | BSD-3-Clause (bundles FFmpeg libraries under LGPL-2.1+) | https://github.com/PyAV-Org/PyAV |
| bottle | 0.13.4 | MIT License | http://bottlepy.org/ |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| clr_loader | 0.3.1 | MIT | https://github.com/pythonnet/clr-loader |
| colorama | 0.4.6 | BSD License | https://github.com/tartley/colorama |
| ctranslate2 | 4.8.2 | MIT | https://opennmt.net |
| docstring_parser | 0.18.0 | MIT License | https://github.com/rr-/docstring_parser |
| faster-whisper | 1.2.1 | MIT License | https://github.com/SYSTRAN/faster-whisper |
| filelock | 4.0.9 | MIT | https://github.com/tox-dev/py-filelock |
| flatbuffers | 25.12.19 | Apache Software License | https://google.github.io/flatbuffers/ |
| fsspec | 2026.9.0 | BSD-3-Clause | https://github.com/fsspec/filesystem_spec |
| h11 | 0.16.0 | MIT License | https://github.com/python-hyper/h11 |
| hf-xet | 1.6.0 | Apache-2.0 | https://github.com/huggingface/xet-core |
| httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore/ |
| httpcore2 | 2.13.1 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| httpx | 0.28.1 | BSD License | https://github.com/encode/httpx |
| httpx2 | 2.13.1 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| huggingface_hub | 1.33.0 | Apache Software License | https://github.com/huggingface/huggingface_hub |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| Jinja2 | 3.1.6 | BSD License | https://github.com/pallets/jinja/ |
| jiter | 0.17.0 | MIT | https://github.com/pydantic/jiter/ |
| kaldi-native-fbank | 1.22.3 | Apache-2.0 | https://github.com/csukuangfj/kaldi-native-fbank |
| markdown-it-py | 4.2.0 | MIT License | https://github.com/executablebooks/markdown-it-py |
| MarkupSafe | 3.0.3 | BSD-3-Clause | https://github.com/pallets/markupsafe/ |
| mdurl | 0.1.2 | MIT License | https://github.com/executablebooks/mdurl |
| mpmath | 1.3.0 | BSD License | http://mpmath.org/ |
| networkx | 3.7 | BSD-3-Clause | https://networkx.org/ |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | https://numpy.org |
| onnxruntime | 1.30.0 | MIT License | https://onnxruntime.ai |
| openai | 3.24.0 | Apache-2.0 | https://github.com/openai/openai-python |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| protobuf | 7.36.2 | 3-Clause BSD License | https://developers.google.com/protocol-buffers/ |
| proxy_tools | 0.1.0 | MIT License | http://github.com/jtushman/proxy_tools |
| PyAudioWPatch | 0.2.12.8 | Apache Software License | https://github.com/s0d3s/PyAudioWPatch/ |
| pycparser | 3.1 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.13.5 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.46.5 | MIT | https://github.com/pydantic/pydantic |
| Pygments | 2.21.0 | BSD-2-Clause | https://pygments.org |
| pythonnet | 3.2.1 | MIT | https://pythonnet.github.io/ |
| pywebview | 6.2.1 | BSD License | https://pywebview.flowrl.com/ |
| PyYAML | 6.0.3 | MIT License | https://pyyaml.org/ |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | https://github.com/mrabarnett/mrab-regex |
| rich | 15.0.0 | MIT License | https://github.com/Textualize/rich |
| safetensors | 0.8.0 | Apache Software License | https://github.com/huggingface/safetensors |
| sentencepiece | 0.2.2 | Apache-2.0 | https://github.com/google/sentencepiece |
| shellingham | 1.5.4 | ISC License (ISCL) | https://github.com/sarugaku/shellingham |
| sniffio | 1.3.1 | MIT License, Apache Software License | https://github.com/python-trio/sniffio |
| sympy | 1.14.0 | BSD License | https://sympy.org |
| tokenizers | 0.23.2 | Apache Software License | https://github.com/huggingface/tokenizers |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | https://tqdm.github.io |
| truststore | 0.10.4 | MIT | https://github.com/sethmlarson/truststore |
| typer | 0.27.2 | MIT | https://github.com/fastapi/typer |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |

## Bundled UI assets

- **Fraunces, Hanken Grotesk, JetBrains Mono** fonts — SIL Open Font License 1.1 — https://openfontlicense.org
- **Lucide** icons 0.469.0 — ISC License — https://lucide.dev/license

## Downloaded at first use (not bundled)

- **Whisper** speech models (OpenAI, MIT) in CTranslate2 format via faster-whisper — from Hugging Face.
- **Opus-MT** translation models (Helsinki-NLP, CC-BY 4.0) in CTranslate2 format — from Hugging Face.
- Optional GPU add-on: **NVIDIA cuBLAS / cuDNN** wheels, NVIDIA software license — from PyPI.
- Only with *Speaker labels* (beta) on: the **CAM++ speaker-verification model** (`speech_campplus_sv_en_voxceleb_16k`) from
  [3D-Speaker](https://github.com/modelscope/3D-Speaker) by Alibaba's Tongyi Lab, Apache-2.0
  ([model card](https://www.modelscope.cn/models/iic/speech_campplus_sv_en_voxceleb_16k)), in the ONNX export published by
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models) (Apache-2.0) — from GitHub.
  It was trained on **VoxCeleb1 and VoxCeleb2** (A. Nagrani, J. S. Chung, W. Xie, A. Zisserman, University of Oxford),
  licensed CC BY 4.0 — https://www.robots.ox.ac.uk/~vgg/data/voxceleb/ . No changes were made to the model.
- Only with *Natural voice* (experimental) on and after pressing Download: **Kokoro-82M** text-to-speech
  (hexgrad, Apache-2.0 — https://huggingface.co/hexgrad/Kokoro-82M) in the ONNX export and voice files published by
  [onnx-community/Kokoro-82M-v1.0-ONNX](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX) (Apache-2.0) — from
  Hugging Face. Dolmi runs the model itself with onnxruntime; it does **not** use the `kokoro-onnx` package, because that
  package and `misaki[en]` pull in espeak-ng (GPL-3.0) through `espeakng-loader` / `phonemizer`. Phonemes come from
  **misaki** (Apache-2.0) with its espeak fallback switched off, which needs **spaCy** and **en_core_web_sm** (MIT) and
  **num2words** (LGPL-2.1, used unmodified as a separate package). These packages are listed in
  `requirements-voice.txt` and are not in the default build yet; without them the system voice speaks.

# macOS (Dolmi.app)

Dolmi.app bundles the components below; the full texts are in each package's
`<package>-<version>.dist-info` folder inside `Dolmi.app/Contents/Resources`. Fonts, icons and the
downloaded models are the same as on Windows (above).

## Runtime

- **Python 3.12**, Python Software Foundation License — https://www.python.org/psf/license/
- **PyInstaller** bootloader, GPL-2.0 with the bootloader exception (allows bundling any program) — https://pyinstaller.org

## Python packages

| Package | Version | Licence | Project |
|---|---|---|---|
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anthropic | 1.13.0 | MIT | https://github.com/anthropics/anthropic-sdk-python |
| anyio | 4.15.1 | MIT | https://anyio.readthedocs.io/en/latest/ |
| av | 19.0.1 | BSD-3-Clause | https://github.com/PyAV-Org/PyAV/issues |
| bottle | 0.13.4 | MIT | http://bottlepy.org/ |
| cffi | 2.1.1 | MIT-0 | https://cffi.readthedocs.io/ |
| click | 8.5.0 | BSD-3-Clause | https://click.palletsprojects.com/page/changes/ |
| ctranslate2 | 4.8.2 | MIT | https://opennmt.net |
| docstring_parser | 0.18.0 | MIT | https://github.com/rr-/docstring_parser |
| faster-whisper | 1.2.1 | MIT | https://github.com/SYSTRAN/faster-whisper |
| filelock | 4.0.12 | MIT | https://py-filelock.readthedocs.io |
| flatbuffers | 25.12.19 | Apache 2.0 | https://google.github.io/flatbuffers/ |
| fsspec | 2026.9.0 | BSD-3-Clause | https://filesystem-spec.readthedocs.io/en/latest/changelog.html |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| hf-xet | 1.7.0 | Apache-2.0 | https://huggingface.co/docs/hub/xet/index |
| httpcore2 | 2.13.1 | BSD-3-Clause | https://github.com/pydantic/httpx2/blob/main/src/httpcore2/CHANGELOG.md |
| httpx2 | 2.13.1 | BSD-3-Clause | https://github.com/pydantic/httpx2/blob/main/src/httpx2/CHANGELOG.md |
| huggingface_hub | 2.2.0 | Apache-2.0 | https://github.com/huggingface/huggingface_hub |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna/blob/master/HISTORY.md |
| jaraco.classes | 3.4.0 | MIT License | https://github.com/jaraco/jaraco.classes |
| jaraco.context | 6.1.2 | MIT | https://github.com/jaraco/jaraco.context |
| jaraco.functools | 4.6.0 | MIT | https://github.com/jaraco/jaraco.functools |
| jiter | 0.17.0 | MIT | https://github.com/pydantic/jiter/ |
| kaldi-native-fbank | 1.22.3 | Apache-2.0 | https://github.com/csukuangfj/kaldi-native-fbank |
| keyring | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| more-itertools | 11.1.0 | MIT | https://more-itertools.readthedocs.io/en/stable/ |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | https://numpy.org |
| onnxruntime | 1.31.0 | MIT License | https://onnxruntime.ai |
| openai | 3.27.0 | Apache-2.0 | https://github.com/openai/openai-python |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://packaging.pypa.io/ |
| protobuf | 7.36.2 | 3-Clause BSD License | https://developers.google.com/protocol-buffers/ |
| proxy_tools | 0.1.0 | MIT | http://github.com/jtushman/proxy_tools |
| pycparser | 3.11 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.14.0 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.0 | MIT | https://github.com/pydantic/pydantic |
| pyobjc-core | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-Cocoa | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-Quartz | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-Security | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-UniformTypeIdentifiers | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-WebKit | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| pywebview | 6.2.1 | BSD 3-Clause License | https://pywebview.flowrl.com/ |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| sentencepiece | 0.2.2 | Apache-2.0 | https://github.com/google/sentencepiece |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | https://github.com/python-trio/sniffio |
| sounddevice | 0.5.6 | MIT | https://python-sounddevice.readthedocs.io/ |
| tokenizers | 0.23.3 | Apache Software License | https://github.com/huggingface/tokenizers |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | https://tqdm.github.io |
| truststore | 0.10.4 | MIT | https://truststore.readthedocs.io |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions/issues |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
