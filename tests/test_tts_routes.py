import conftest_paths  # noqa: F401
import base64, io, os, tempfile, unittest, wave
from unittest import mock

import routes, tts
from routes import ApiError, Req


def _wav(n=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


class FakeSpeaker:
    busy = False

    def __init__(self, out=b"RIFFfake", err=None):
        self.out, self.err, self.calls, self.status = out, err, [], None

    def speak(self, cfg, req):
        self.calls.append(req)
        if self.err:
            if self.status:
                raise tts.TtsError(self.err, self.status)
            raise tts.TtsError(self.err)
        return self.out


class TtsRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"router_host": "127.0.0.1", "server_bin": "",
                    "tts_dir": self.tmp.name}
        p1 = mock.patch.object(routes, "cfg", lambda: dict(self.cfg))
        self.speaker = FakeSpeaker()
        p2 = mock.patch.object(routes, "TTS", self.speaker)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_routes_are_registered(self):
        self.assertIn("/api/tts/status", routes.GET_ROUTES)
        self.assertIn("/api/tts/progress", routes.GET_ROUTES)
        for p in ("/v1/audio/speech", "/api/tts/speak", "/api/tts/get",
                  "/api/tts/voice", "/api/tts/voice/delete"):
            self.assertIn(p, routes.POST_ROUTES)

    def test_status(self):
        st, body = routes.GET_ROUTES["/api/tts/status"](Req())
        self.assertEqual(st, 200)
        self.assertFalse(body["ready"])
        self.assertIn("busy", body)
        self.assertIn("download", body)
        self.assertFalse(body["needs_key"])
        self.cfg.update(router_host="0.0.0.0", router_api_key="k" * 40)
        self.assertTrue(routes.GET_ROUTES["/api/tts/status"](Req())[1]["needs_key"])
        self.assertNotIn("k" * 40, str(routes.GET_ROUTES["/api/tts/status"](Req())[1]))

    def test_openai_speech_returns_audio(self):
        st, out, ctype = routes.POST_ROUTES["/v1/audio/speech"](
            Req(body={"model": "tts-1", "input": "hi", "voice": "alloy"}))
        self.assertEqual((st, out, ctype), (200, b"RIFFfake", "audio/wav"))

    def test_pcm_content_type(self):
        st, out, ctype = routes.POST_ROUTES["/v1/audio/speech"](
            Req(body={"input": "hi", "response_format": "pcm"}))
        self.assertEqual(ctype, "audio/pcm")

    def test_openai_speech_bad_input_is_400_in_openai_shape(self):
        st, body = routes.POST_ROUTES["/v1/audio/speech"](Req(body={"input": ""}))
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["type"], "invalid_request_error")

    def test_openai_speech_not_ready_is_503(self):
        self.speaker.err, self.speaker.status = "text-to-speech is not set up: missing model", 503
        st, body = routes.POST_ROUTES["/v1/audio/speech"](Req(body={"input": "hi"}))
        self.assertEqual(st, 503)
        self.assertIn("not set up", body["error"]["message"])

    def test_openai_speech_requires_the_key_off_loopback(self):
        self.cfg.update(router_host="0.0.0.0", router_api_key="k" * 40)
        st, body = routes.POST_ROUTES["/v1/audio/speech"](Req(body={"input": "hi"}, headers={}))
        self.assertEqual(st, 401)
        st, out, _ = routes.POST_ROUTES["/v1/audio/speech"](
            Req(body={"input": "hi"}, headers={"authorization": "Bearer " + "k" * 40}))
        self.assertEqual(st, 200)

    def test_panel_speak_raises_api_errors(self):
        self.speaker.err = "llama-tts exited with 1"
        with self.assertRaises(ApiError) as cm:
            routes.POST_ROUTES["/api/tts/speak"](Req(body={"input": "hi"}))
        self.assertEqual(cm.exception.status, 500)

    def test_save_and_delete_voice(self):
        data = base64.b64encode(_wav()).decode()
        st, body = routes.POST_ROUTES["/api/tts/voice"](Req(body={"name": "me", "wav_b64": data}))
        self.assertEqual(body["voices"], ["me"])
        st, body = routes.POST_ROUTES["/api/tts/voice/delete"](Req(body={"name": "me"}))
        self.assertEqual(body["voices"], [])

    def test_save_voice_rejects_garbage(self):
        for b in ({"name": "me", "wav_b64": "%%%"}, {"name": "../me", "wav_b64": ""},
                  {"name": "me", "wav_b64": base64.b64encode(b"nope").decode()}):
            with self.assertRaises(ApiError):
                routes.POST_ROUTES["/api/tts/voice"](Req(body=b))

    def test_get_model_downloads_into_the_tts_folder(self):
        with mock.patch.object(routes.TTS_DOWNLOADS, "start", return_value=True) as start:
            st, body = routes.POST_ROUTES["/api/tts/get"](Req(body={}))
        self.assertTrue(body["started"])
        repo, files, dest = start.call_args[0]
        self.assertEqual(repo, tts.REPO)
        self.assertEqual(files, tts.REPO_FILES)
        self.assertEqual(dest, tts.models_dir(self.cfg))

    def test_get_a_catalog_model_into_its_own_subfolder(self):
        e = tts.catalog_entry("pocket-tts-en")
        with mock.patch.object(routes.TTS_DOWNLOADS, "start", return_value=True) as start, \
             mock.patch.object(routes.threading, "Thread") as thread:
            st, body = routes.POST_ROUTES["/api/tts/get"](Req(body={"id": "pocket-tts-en"}))
        self.assertTrue(body["started"])
        repo, files, dest = start.call_args[0]
        self.assertEqual((repo, files), (e["repo"], e["files"]))
        self.assertEqual(dest, os.path.join(tts.models_dir(self.cfg), "pocket-tts-en"))
        thread.assert_called_once()                       # the CC0 starter voice comes along

    def test_get_unknown_catalog_id_is_400(self):
        with self.assertRaises(ApiError) as cm:
            routes.POST_ROUTES["/api/tts/get"](Req(body={"id": "kokoro"}))
        self.assertEqual(cm.exception.status, 400)

    def test_error_status_comes_from_the_tts_error(self):
        self.speaker.err = "Pocket TTS needs a voice clip"
        self.speaker.status = 400
        st, body = routes.POST_ROUTES["/v1/audio/speech"](Req(body={"input": "hi"}))
        self.assertEqual(st, 400)
        with self.assertRaises(ApiError) as cm:
            routes.POST_ROUTES["/api/tts/speak"](Req(body={"input": "hi"}))
        self.assertEqual(cm.exception.status, 400)

    def test_tts_downloads_never_register_into_models_ini(self):
        self.assertIsNot(routes.TTS_DOWNLOADS, routes.DOWNLOADS)
        self.assertIsNone(routes.TTS_DOWNLOADS.on_done)


if __name__ == "__main__":
    unittest.main()
