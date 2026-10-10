import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from restore_archive import restore, validate_directory, validate_index, parse_json
from deploy_cloudflare import deploy

def fixture(race_id):
    return {"schema_version": 1, "id": race_id, "state": "ready", "drivers": [{}] * 5,
            "results": [{}] * 5, "track": [[0, 0], [1, 1], [2, 2]], "total_laps": 78, "duration": 10}

def index_for(*ids):
    return {"schema_version": 1, "races": [{"id": i, "year": int(i[:4]), "round": int(i[5:]),
            "state": "ready", "replay_url": f"races/{i}.json"} for i in ids]}

def pack(value):
    return json.dumps(value).encode(), value

class CloudflareTests(unittest.TestCase):
    def test_full_live_archive_is_restored_on_a_fresh_runner(self):
        index = index_for("2024-08", "2026-01")
        responses = {"index.json": index, "races/2024-08.json": fixture("2024-08"),
                     "races/2026-01.json": fixture("2026-01")}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "public"
            restore("https://example.pages.dev", out, lambda u: pack(responses[u.split(".dev/")[1]]))
            self.assertEqual(json.loads((out / "index.json").read_text()), index)
            self.assertTrue((out / "races/2024-08.json").exists())
            self.assertTrue((out / "races/2026-01.json").exists())

    def test_interrupted_download_does_not_replace_old_index_or_replays(self):
        index = index_for("2024-08", "2026-01")
        def download(url):
            if url.endswith("index.json"): return pack(index)
            if url.endswith("2024-08.json"): return pack(fixture("2024-08"))
            raise RuntimeError("Download unavailable")
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "public"
            (out / "races").mkdir(parents=True)
            (out / "index.json").write_text("original index")
            (out / "races/2024-08.json").write_text("original replay")
            with self.assertRaises(RuntimeError): restore("https://example.pages.dev", out, download)
            self.assertEqual((out / "index.json").read_text(), "original index")
            self.assertEqual((out / "races/2024-08.json").read_text(), "original replay")

    def test_mismatched_replay_stops_archive_restoration(self):
        def download(url):
            return pack(index_for("2024-08") if url.endswith("index.json") else fixture("2026-01"))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "mismatched"):
                restore("https://example.pages.dev", Path(folder) / "public", download)

    def test_unexpected_replay_path_and_duplicate_id_are_rejected(self):
        index = index_for("2024-08")
        index["races"][0]["replay_url"] = "../../private.json"
        with self.assertRaises(ValueError): validate_index(index)
        with self.assertRaises(ValueError): validate_index(index_for("2024-08", "2024-08"))

    def test_missing_replay_stops_deployment(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "_headers").write_text("/*\n  Access-Control-Allow-Origin: *\n")
            (out / "404.html").write_text("Missing file")
            (out / "index.json").write_text(json.dumps(index_for("2024-08")))
            with self.assertRaises(FileNotFoundError): validate_directory(out)

    def test_oversized_file_stops_deployment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "oversized.json"
            with path.open("wb") as file: file.truncate(25 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(ValueError, "25 MiB"): validate_directory(folder)

    def test_non_finite_json_is_rejected(self):
        with self.assertRaises(ValueError): parse_json(b'{"sample": NaN}')

    def test_deployment_targets_cloudflares_actual_production_branch(self):
        env = {"CLOUDFLARE_API_TOKEN": "test-token", "CLOUDFLARE_ACCOUNT_ID": "a" * 32}
        response = {"success": True, "result": {"production_branch": "production"}}
        with patch.dict(os.environ, env, clear=True), \
             patch("deploy_cloudflare.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())), \
             patch("deploy_cloudflare.subprocess.run") as run:
            deploy("public")
            self.assertIn("--branch=production", run.call_args.args[0])
            self.assertNotIn("test-token", " ".join(run.call_args.args[0]))

    def test_missing_credentials_do_not_start_deployment(self):
        with patch.dict(os.environ, {}, clear=True), patch("deploy_cloudflare.subprocess.run") as run:
            with self.assertRaises(ValueError): deploy("public")
            run.assert_not_called()

if __name__ == "__main__": unittest.main()
