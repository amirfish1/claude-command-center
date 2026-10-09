#!/usr/bin/env bash
# Install CCC's local voice (Kokoro text-to-speech) into ~/.ccc/local-tts.
#
# Used by the Speak button (scripts/local_tts_server.py) and by Car Mode
# (ccc-voice), which also runs whisper.cpp for speech-to-text and downloads
# its model on first start. Re-running is safe: files already present are kept.
#
#   scripts/install_local_speech.sh
set -euo pipefail

DIR="${CCC_LOCAL_TTS_DIR:-$HOME/.ccc/local-tts}"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
mkdir -p "$DIR"

if [[ ! -x "$DIR/venv/bin/python" ]]; then
  echo "Creating $DIR/venv"
  python3 -m venv "$DIR/venv"
fi
"$DIR/venv/bin/pip" install --quiet --upgrade kokoro-onnx soundfile lameenc

# int8 is a third of the size and fast on Apple Silicon, but several times
# slower than fp32 on x86 CPUs without VNNI, so x86 gets the fp32 model.
if [[ "$(uname -m)" == "x86_64" ]]; then
  MODEL_FILE="kokoro.fp32.onnx"; MODEL_URL="$BASE/kokoro-v1.0.onnx"
else
  MODEL_FILE="kokoro.int8.onnx"; MODEL_URL="$BASE/kokoro-v1.0.int8.onnx"
fi
fetch() {  # fetch <file> <url>: download to a temp name, then move into place
  [[ -s "$DIR/$1" ]] && { echo "have $1"; return; }
  echo "Downloading $1"
  curl -fL --progress-bar -o "$DIR/$1.part" "$2"
  mv "$DIR/$1.part" "$DIR/$1"
}
fetch "$MODEL_FILE" "$MODEL_URL"
fetch voices.bin "$BASE/voices-v1.0.bin"

"$DIR/venv/bin/python" - "$DIR" <<'EOF'
import os, sys, time
from kokoro_onnx import Kokoro
d = sys.argv[1]
model = next(os.path.join(d, n) for n in ("kokoro.fp32.onnx", "kokoro.int8.onnx")
             if os.path.exists(os.path.join(d, n)))
k = Kokoro(model, os.path.join(d, "voices.bin"))
t = time.time()
samples, rate = k.create("Local voice ready.", voice="af_heart")
print("Kokoro OK: %s, %.1fs of audio in %.1fs" % (os.path.basename(model), len(samples) / rate, time.time() - t))
EOF
echo "Done. Speak uses this voice by default; restart CCC if its voice server was already running."
