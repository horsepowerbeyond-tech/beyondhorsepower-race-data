from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
from export_races import atomic_json,load_session,metadata

class WorkerTests(unittest.TestCase):
    def test_invalid_new_json_does_not_replace_last_successful_file(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"index.json";atomic_json(file,{"schema_version":1})
            with self.assertRaises(ValueError):atomic_json(file,{"bad":float("nan")})
            self.assertEqual(json.loads(file.read_text()),{"schema_version":1})
    def test_running_race_is_not_published(self):
        session=SimpleNamespace(load=lambda **kwargs:None,laps=pd.DataFrame({"LapNumber":[1]}),pos_data={"1":[1]},session_start_time=pd.Timedelta(seconds=10),results=pd.DataFrame({"Position":range(1,7)}),session_status=pd.DataFrame({"Status":["Started"]}))
        with patch("export_races.fastf1.get_session",return_value=session):
            with self.assertRaisesRegex(ValueError,"not finished"):load_session(2024,8)
    def test_unknown_calendar_time_is_not_invented(self):
        event=pd.Series({"RoundNumber":8,"EventName":"Monaco Grand Prix","Location":"Monaco","Country":"Monaco","EventFormat":"conventional","Session5":"Race","Session5DateUtc":pd.NaT})
        self.assertIsNone(metadata(event,2024)["race_start"])

if __name__=="__main__":unittest.main()
