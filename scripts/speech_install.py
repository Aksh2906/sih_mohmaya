"""Download public multilingual model weights once; never upload recordings."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from privacy_guard.audio import LocalTranscriber  # noqa: E402


def main():
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from faster_whisper.utils import download_model

    transcriber = LocalTranscriber()
    if not transcriber.configured:
        transcriber.model_path.mkdir(parents=True, exist_ok=True)
        download_model("small", output_dir=str(transcriber.model_path))
    print("Multilingual local speech model is ready. No API key is required.")


if __name__ == "__main__":
    main()
