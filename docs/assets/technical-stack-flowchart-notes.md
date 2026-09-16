# Technical stack flowchart

Target architecture, not a delivered-feature inventory. Concise labels replace explanatory sentences.

- Local visual backbone candidate: [apple/mobilevit-small](https://huggingface.co/apple/mobilevit-small). UI adaptation/export still needs validation; the classification backbone alone does not detect all PII.
- Face model candidate: [MediaPipe BlazeFace](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/face_detection.md).
- Self-hosted reasoning candidate: [LiquidAI/LFM2.5-VL-1.6B](https://huggingface.co/LiquidAI/LFM2.5-VL-1.6B). Task quality, tool schemas and integration still require testing.
- Tesseract.js: proposed browser OCR path; existing Python document OCR uses pytesseract. The earlier PP-OCRv6 suggestion is not represented as a confirmed choice.
- ONNX Runtime Web / WebGPU / WASM describe proposed browser runtime options, not a claim that every listed model shares the same backend. MediaPipe and Tesseract.js have their own runtimes.
- Browser Use 0.13.10 and Python service dependencies are taken from the repository's pyproject.toml.
- Named cache, masking and orchestration components are target modules. Model selections are starred in the chart.

Private values remain local to processing and are disclosed to authorized destination websites during execution.
