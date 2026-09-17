"""Retired recording endpoints cannot accept input or start work."""

from test_workflow import client_for


async def test_recording_api_removed_and_settings_only_expose_current_features(tmp_path):
    app, _, client = await client_for(tmp_path)
    async with client:
        assert not hasattr(app.state, "transcriber")
        schema = (await client.get("/api/v1/schema")).json()
        assert not any("/audio/" in path for path in schema["paths"])
        for endpoint in ("/api/v1/settings", "/api/v1/status"):
            response = await client.get(endpoint)
            assert response.status_code == 200
            assert "speech_ready" not in response.text
            assert "speech_provider" not in response.text
        response = await client.post(
            "/api/v1/audio/transcribe", content=b"retired-input", headers={"Content-Type": "audio/webm"}
        )
        assert response.status_code in (404, 405)
        assert not app.state.manager.tasks
        assert "media-src 'none'" in response.headers["Content-Security-Policy"]
