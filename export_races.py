"""Prepare static FastF1 race files. Run outside Shopify; never on a visitor request."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import json
import logging
import math
import os
from pathlib import Path
import re
import sys

import fastf1
import numpy as np
import pandas as pd

LOG = logging.getLogger("bh-races")
STATUSES = {"1":"Green flag", "2":"Yellow flag", "4":"Safety car", "5":"Red flag", "6":"Virtual safety car", "7":"VSC ending"}

def number(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default

def text(value, default=""):
    return default if value is None or pd.isna(value) else str(value)

def seconds(value, default=None):
    if value is None or pd.isna(value):
        return default
    return number(value.total_seconds(), default)

def iso(value):
    date = pd.Timestamp(value)
    if pd.isna(date):
        return None
    if date.tzinfo is None:
        date = date.tz_localize("UTC")
    return date.tz_convert("UTC").isoformat().replace("+00:00", "Z")

def atomic_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    temporary.replace(path)

def metadata(event, year):
    race_date = None
    for index in range(1, 6):
        if text(event.get(f"Session{index}")) == "Race":
            race_date = event.get(f"Session{index}DateUtc")
            if race_date is None or pd.isna(race_date):
                # EventDate contains a date only; do not invent a start time.
                race_date = None
            break
    round_number = int(event.RoundNumber)
    return {"id":f"{year}-{round_number:02d}", "year":int(year), "round":round_number,
            "name":text(event.EventName), "location":text(event.Location), "country":text(event.Country),
            "race_start":iso(race_date) if race_date is not None else None,
            "format":text(event.EventFormat), "state":"scheduled"}

def load_session(year, round_number):
    session = fastf1.get_session(year, round_number, "R")
    session.load(laps=True, telemetry=True, weather=True, messages=True)
    if session.laps.empty or not session.pos_data or session.session_start_time is None:
        raise ValueError("Published race timing/position data is not ready")
    if session.results.empty or len(session.results) < 5:
        raise ValueError("Race classification is not ready")
    status = session.session_status
    if status.empty or not status.Status.isin(["Finished", "Finalised"]).any():
        raise ValueError("The session has not finished")
    return session

def export_session(session, meta, sample_interval=1.0):
    start = seconds(session.session_start_time)
    all_laps = session.laps
    race_end = seconds(all_laps.Time.max())
    duration = race_end - start + 8
    if duration <= 0:
        raise ValueError("Invalid race duration")
    results = []
    for _, row in session.results.iterrows():
        position = number(row.get("Position"))
        if position is None or position < 1:
            continue
        results.append({"position":int(position), "code":text(row.get("Abbreviation")),
                        "name":text(row.get("FullName")) or text(row.get("Abbreviation")),
                        "team":text(row.get("TeamName")), "grid":number(row.get("GridPosition")),
                        "points":number(row.get("Points")), "status":text(row.get("Status")),
                        "classified":text(row.get("ClassifiedPosition")), "time_seconds":seconds(row.get("Time"))})
    results.sort(key=lambda r:r["position"])
    if not results or results[0]["position"] != 1 or len({r["position"] for r in results}) != len(results):
        raise ValueError("Incomplete or duplicate classification positions")
    result_by_code = {r["code"]:r for r in results}
    drivers = []
    for driver_number in session.drivers:
        driver = session.get_driver(driver_number)
        code = text(driver.Abbreviation)
        if code not in result_by_code:
            continue
        laps = all_laps.pick_drivers(driver_number).sort_values("LapNumber")
        positions = session.pos_data.get(str(driver_number))
        if positions is None or positions.empty:
            continue
        positions = positions.sort_values("SessionTime").drop_duplicates("SessionTime")
        pos_times = positions.SessionTime.dt.total_seconds().to_numpy() - start
        valid = np.flatnonzero((pos_times >= -2) & (pos_times <= duration+2))
        if len(valid) < 3:
            continue
        positions = positions.iloc[valid]
        pos_times = pos_times[valid]
        cars = session.car_data.get(str(driver_number))
        car_times = cars.SessionTime.dt.total_seconds().to_numpy()-start if cars is not None and not cars.empty else np.array([])
        samples, last_time = [], -math.inf
        for index, (_, row) in enumerate(positions.iterrows()):
            t = float(pos_times[index])
            if t-last_time < sample_interval:
                continue
            x, y = number(row.get("X")), number(row.get("Y"))
            if x is None or y is None:
                continue
            last_time = t
            car_index = np.searchsorted(car_times,t,side="right")-1
            car = cars.iloc[car_index] if car_index>=0 and t-car_times[car_index]<=2 else {}
            samples.append([round(t,3), round(x,1), round(y,1), number(car.get("Speed")),
                            number(car.get("nGear")), number(car.get("Throttle")),
                            None if car.get("Brake") is None or pd.isna(car.get("Brake")) else int(bool(car.get("Brake"))),
                            number(car.get("DRS")), int(text(row.get("Status"))=="OnTrack")])
        timed_laps, stints, stops = [], {}, []
        for _, lap in laps.iterrows():
            lap_number = int(lap.LapNumber)
            lap_start = seconds(lap.get("LapStartTime"))
            lap_end = seconds(lap.get("Time"))
            if lap_end is not None:
                timed_laps.append({"number":lap_number, "start":max(0,(lap_start if lap_start is not None else lap_end)-start),
                                   "end":max(0,lap_end-start), "seconds":seconds(lap.get("LapTime")),
                                   "position":number(lap.get("Position")), "compound":text(lap.get("Compound"))})
            stint_number = int(number(lap.get("Stint"),0))
            compound = text(lap.get("Compound"),"UNKNOWN")
            if stint_number not in stints:
                stints[stint_number] = {"compound":compound, "from_lap":lap_number, "to_lap":lap_number}
            stints[stint_number]["to_lap"] = lap_number
            pit_in = seconds(lap.get("PitInTime"))
            if pit_in is not None:
                after = laps.loc[(laps.LapNumber>=lap_number)&laps.PitOutTime.notna(),"PitOutTime"]
                exits = [seconds(t) for t in after if seconds(t)>pit_in]
                stops.append({"lap":lap_number, "entry":max(0,pit_in-start),
                              "pit_lane_seconds":round(min(exits)-pit_in,3) if exits else None})
        colour = text(driver.get("TeamColor"))
        drivers.append({"code":code, "number":str(driver_number), "name":result_by_code[code]["name"],
                        "team":result_by_code[code]["team"], "color":"#"+colour if re.fullmatch(r"[0-9a-fA-F]{6}",colour) else "#708895",
                        "samples":samples, "laps":timed_laps, "stints":list(stints.values()), "pit_stops":stops})
    drivers.sort(key=lambda d:result_by_code[d["code"]]["position"])
    if len(drivers)<5:
        raise ValueError("Insufficient driver position data")
    fastest = all_laps.pick_fastest()
    if fastest is None:
        raise ValueError("No valid reference lap")
    reference = fastest.get_pos_data()
    reference = reference.loc[reference.Status=="OnTrack"].dropna(subset=["X","Y"])
    step = max(1,len(reference)//700)
    track = [[round(float(r.X),1),round(float(r.Y),1)] for _,r in reference.iloc[::step].iterrows()]
    if len(track)<3:
        raise ValueError("Circuit position data is unavailable")
    events = []
    for _, event in session.track_status.iterrows():
        t = seconds(event.get("Time"))
        if t is not None and 0<=t-start<=duration:
            label=STATUSES.get(text(event.get("Status")),text(event.get("Message"),"Track status update"))
            if not events or label!=events[-1]["label"]:
                events.append({"time":round(t-start,3),"label":label})
    weather = session.weather_data
    weather_summary={}
    if weather is not None and not weather.empty:
        for field,key in [("AirTemp","air_c"),("TrackTemp","track_c")]:
            n=number(weather[field].median()) if field in weather else None
            weather_summary[key]=round(n,1) if n is not None else None
        weather_summary["rain_reported"]=bool(weather.Rainfall.any()) if "Rainfall" in weather else None
    pole=next((r["name"] for r in results if r["grid"]==1),None)
    return {"schema_version":1, **meta, "state":"ready", "source":"FastF1", "duration":round(duration,3),
            "total_laps":int(all_laps.LapNumber.max()), "results":results, "drivers":drivers, "track":track,
            "events":events, "weather":weather_summary, "pole":pole,
            "fastest_lap":{"code":text(fastest.Driver),"lap":int(fastest.LapNumber),"seconds":seconds(fastest.LapTime)},
            "updated_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")}

def run(args):
    out = Path(args.output)
    cache = Path(args.cache);cache.mkdir(parents=True,exist_ok=True);fastf1.Cache.enable_cache(cache)
    index_path=out/"index.json"
    index=json.loads(index_path.read_text()) if index_path.exists() else {"schema_version":1,"races":[]}
    existing={r["id"]:r for r in index["races"]}
    schedule=fastf1.get_event_schedule(args.year,include_testing=False)
    now=datetime.now(timezone.utc)
    pending=[]
    for _, event in schedule.iterrows():
        meta=metadata(event,args.year)
        prior=existing.get(meta["id"],{})
        if prior.get("state")=="ready":
            meta.update(prior)
        if meta["race_start"]:
            date=datetime.fromisoformat(meta["race_start"].replace("Z","+00:00"))
            if date<=now:
                if meta["state"]!="ready":meta["state"]="processing"
                if date<=now-timedelta(hours=args.delay_hours):
                    recent=date>now-timedelta(days=7)
                    if args.round==meta["round"] or (args.round is None and (meta["state"]!="ready" or (recent and args.refresh))):
                        pending.append(meta)
        existing[meta["id"]]=meta
    if args.round is not None:
        pending=[m for m in pending if m["round"]==args.round]
        if not pending:raise ValueError("Selected round is not yet eligible or not in the calendar")
    pending.sort(key=lambda m:m["round"],reverse=True)
    failed=[]
    for meta in pending[:args.max_races]:
        try:
            LOG.info("Preparing %s %s",meta["year"],meta["name"])
            session=load_session(meta["year"],meta["round"])
            data=export_session(session,meta,args.sample_interval)
            relative=f"races/{meta['id']}.json"
            atomic_json(out/relative,data)
            meta.update({"state":"ready", "replay_url":relative, "podium":data["results"][:3],
                         "total_laps":data["total_laps"], "fastest_lap":data["fastest_lap"], "updated_at":data["updated_at"]})
            LOG.info("Prepared %s: %s drivers, %s laps",meta["id"],len(data["drivers"]),data["total_laps"])
        except Exception as error:
            failed.append(meta["id"]);LOG.error("%s remains pending: %s",meta["id"],error)
    index["races"]=sorted(existing.values(),key=lambda r:(r["year"],r["round"]))
    index["updated_at"]=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    atomic_json(index_path,index)
    if failed and args.round is not None:raise RuntimeError("The selected race could not be prepared")
    if failed:LOG.warning("Will retry unavailable races on the next scheduled run: %s",", ".join(failed))

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year",type=int,default=datetime.now(timezone.utc).year)
    p.add_argument("--round",type=int)
    p.add_argument("--output",default="public")
    p.add_argument("--cache",default=".fastf1-cache")
    p.add_argument("--max-races",type=int,default=2)
    p.add_argument("--delay-hours",type=float,default=5)
    p.add_argument("--sample-interval",type=float,default=1)
    p.add_argument("--refresh",action="store_true")
    return p

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    arguments=parser().parse_args()
    if arguments.sample_interval<=0 or arguments.max_races<1:sys.exit("Sample interval and maximum races must be positive")
    run(arguments)
