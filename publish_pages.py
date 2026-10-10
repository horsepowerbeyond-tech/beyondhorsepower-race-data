"""Create/update owned Shopify race pages and the Race Centre summary. No tokens in theme code."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import html
import json
import logging
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from export_races import atomic_json

LOG=logging.getLogger("bh-publish")
START="<!-- BHR-AUTO-START -->";END="<!-- BHR-AUTO-END -->"
e=lambda value:html.escape(str(value if value is not None else ""),quote=True)

def format_lap(value):
    if value is None:return "Unavailable"
    return f"{int(value//60)}:{value%60:06.3f}"

def format_date(value):
    if not value:return "Start time to be confirmed"
    date=datetime.fromisoformat(value.replace("Z","+00:00"))
    return date.strftime("%d %B %Y, %H:%M UTC")

def protected_body(current, generated):
    if not current:return f"{START}\n{generated}\n{END}"
    if START not in current or END not in current or current.count(START)!=1 or current.count(END)!=1:
        raise ValueError("Page has unmarked content. Add the BHR-AUTO markers around the generated summary before enabling sync.")
    return current[:current.index(START)+len(START)]+"\n"+generated+"\n"+current[current.index(END):]

def race_body(data, hub_path):
    winner=data["results"][0]["name"]
    fastest=data["fastest_lap"]
    content=f'<div class="bhr-safe-content"><h2>Race summary</h2><p>{e(winner)} finished first in the {data["year"]} {e(data["name"])}. The race covered {data["total_laps"]} laps. {e(fastest["code"])} recorded the fastest available timed lap of {format_lap(fastest["seconds"])} on lap {fastest["lap"]}.</p><p>Location: {e(data["location"])} · {e(data["country"])}. Scheduled start: {e(format_date(data["race_start"]))}.</p></div>'
    content+='<h2>Final race classification</h2><div class="bhr-table-wrap"><table><thead><tr><th>Position</th><th>Driver</th><th>Team</th><th>Grid</th><th>Points</th><th>Status</th></tr></thead><tbody>'
    for r in data["results"]:
        content+='<tr>'+''.join('<td>'+e(v)+'</td>' for v in [r["classified"] or r["position"],r["name"],r["team"],int(r["grid"]) if r["grid"] is not None else "—",f'{r["points"]:g}' if r["points"] is not None else "—",r["status"] or "Unavailable"])+"</tr>"
    content+='</tbody></table></div><h2>Tyre strategies and pit stops</h2><div class="bhr-table-wrap"><table><thead><tr><th>Driver</th><th>Tyre stints</th><th>Pit-lane entries</th></tr></thead><tbody>'
    for d in data["drivers"]:
        stints=" → ".join(f'{s["compound"].title()} (laps {s["from_lap"]}–{s["to_lap"]})' for s in d["stints"])
        entries=", ".join(f'Lap {stop["lap"]}' for stop in d["pit_stops"]) or "None recorded"
        content+=f'<tr><td>{e(d["name"])}</td><td>{e(stints)}</td><td>{e(entries)}</td></tr>'
    content+='</tbody></table></div><p>Tyre stints may include changes during a race stoppage. Pit-lane entries are timing records, not stationary tyre-change durations.</p>'
    weather=data.get("weather",{})
    details=[]
    if weather.get("air_c") is not None:details.append(f'Median air temperature: {weather["air_c"]}°C')
    if weather.get("track_c") is not None:details.append(f'Median track temperature: {weather["track_c"]}°C')
    if weather.get("rain_reported") is not None:details.append('Rain was reported during the session' if weather["rain_reported"] else 'No rain was reported in the weather feed')
    if details:content+='<h2>Session conditions</h2><p>'+e(". ".join(details))+'.</p>'
    content+=f'<p>Source: FastF1 session data. Updated {e(format_date(data["updated_at"]))}. Missing source fields are shown as unavailable.</p><p><a href="{e(hub_path)}">Browse all race results and replays</a></p>'
    return content

def hub_body(index, replay_path):
    content='<h2>Race results and replay archive</h2><p>Explore completed race results, tyre strategies, lap comparisons and animated driver positions. New races are added after the published data has been processed.</p><ul>'
    for r in sorted(index["races"],key=lambda r:(r["year"],r["round"]),reverse=True):
        if r["state"]=="ready":
            path=r.get("page_url") or f'{replay_path}?race={r["id"]}'
            podium=", ".join(d["name"] for d in r.get("podium",[])[:3])
            content+=f'<li><a href="{e(path)}">{r["year"]} {e(r["name"])} — results and replay</a>'+(f' · Podium: {e(podium)}' if podium else '')+'</li>'
    content+='</ul><h2>Upcoming race weekends</h2><ul>'
    now=datetime.now(timezone.utc)
    for r in sorted(index["races"],key=lambda r:r.get("race_start") or "9999"):
        if r.get("race_start") and datetime.fromisoformat(r["race_start"].replace("Z","+00:00"))>now:
            content+=f'<li>{r["year"]} {e(r["name"])} · {e(format_date(r["race_start"]))}</li>'
    return content+'</ul><p>The interactive calendar displays race start times in India Standard Time.</p>'

def admin_token(store):
    """Fetch once per worker run for an app and store in the same organization."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com",store):
        raise ValueError("SHOPIFY_STORE must be your permanent name.myshopify.com hostname")
    client_id=os.environ.get("SHOPIFY_CLIENT_ID","")
    client_secret=os.environ.get("SHOPIFY_CLIENT_SECRET","")
    if client_id or client_secret:
        if not client_id or not client_secret:
            raise ValueError("Set both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET")
        payload=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":client_id,"client_secret":client_secret}).encode()
        request=urllib.request.Request(f"https://{store}/admin/oauth/access_token",data=payload,headers={"Content-Type":"application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request,timeout=45) as response:result=json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Shopify token request returned HTTP {error.code}; verify installation, scopes and app/store organization") from None
        if not result.get("access_token"):
            raise RuntimeError("Shopify token response did not contain an access token")
        return result["access_token"]
    token=os.environ.get("SHOPIFY_ADMIN_TOKEN","")
    if not token:
        raise ValueError("Set Shopify client credentials or a valid SHOPIFY_ADMIN_TOKEN on the worker")
    return token

class Shopify:
    def __init__(self,store,token):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com",store):raise ValueError("SHOPIFY_STORE must be your permanent name.myshopify.com hostname")
        self.endpoint=f'https://{store}/admin/api/2026-10/graphql.json';self.token=token
    def graphql(self,query,variables):
        payload=json.dumps({"query":query,"variables":variables}).encode()
        for attempt in range(4):
            request=urllib.request.Request(self.endpoint,data=payload,headers={"Content-Type":"application/json","X-Shopify-Access-Token":self.token})
            try:
                with urllib.request.urlopen(request,timeout=45) as response:result=json.load(response)
            except urllib.error.HTTPError as error:
                if error.code in [429,502,503,504] and attempt<3:time.sleep(2**attempt);continue
                raise RuntimeError(f"Shopify returned HTTP {error.code}") from None
            if result.get("errors"):
                if any(e.get("extensions",{}).get("code")=="THROTTLED" for e in result["errors"]) and attempt<3:time.sleep(2**attempt);continue
                raise RuntimeError("Shopify GraphQL errors: "+json.dumps(result["errors"]))
            return result["data"]
        raise RuntimeError("Shopify request retries exhausted")
    def find(self,handle):
        data=self.graphql('query FindPage($query:String!){pages(first:100,query:$query){nodes{id handle title body templateSuffix publishedAt metafields(first:20,namespace:"bh_racing"){nodes{key value}}}}}',{"query":"handle:"+handle})
        return next((p for p in data["pages"]["nodes"] if p["handle"]==handle),None)
    def upsert(self,handle,title,summary,suffix,owner_key,owner_value,replay_url=None,publish=False,adopt_hub=False):
        page=self.find(handle)
        if page:
            owned={m["key"]:m["value"] for m in page["metafields"]["nodes"]}
            if owned.get(owner_key)!=owner_value:
                if not (adopt_hub and suffix=="race-centre" and page["templateSuffix"]==suffix):raise ValueError(f"Will not overwrite unrelated existing page: {handle}")
                current=page["body"]
                if START not in current:raise ValueError("For the manually created hub, add BHR-AUTO markers before enabling page sync")
            body=protected_body(page["body"],summary)
        else:body=protected_body("",summary)
        metafields=[{"namespace":"bh_racing","key":owner_key,"type":"single_line_text_field","value":owner_value}]
        if replay_url:metafields.append({"namespace":"bh_racing","key":"replay_url","type":"url","value":replay_url})
        values={"title":title,"handle":handle,"body":body,"templateSuffix":suffix,"isPublished":publish or bool(page and page.get("publishedAt")),"metafields":metafields}
        if page and page["title"]==title and page["body"]==body and page["templateSuffix"]==suffix and bool(page.get("publishedAt"))==values["isPublished"] and all(owned.get(m["key"])==m["value"] for m in metafields):
            return page
        if page:
            response=self.graphql('mutation Update($id:ID!,$page:PageUpdateInput!){pageUpdate(id:$id,page:$page){page{id handle publishedAt}userErrors{field message}}}',{"id":page["id"],"page":values})["pageUpdate"]
        else:
            response=self.graphql('mutation Create($page:PageCreateInput!){pageCreate(page:$page){page{id handle publishedAt}userErrors{field message}}}',{"page":values})["pageCreate"]
        if response["userErrors"]:raise RuntimeError(json.dumps(response["userErrors"]))
        return response["page"]

def run(args):
    directory=Path(args.output);index=json.loads((directory/"index.json").read_text())
    base=os.environ.get("PUBLIC_BASE_URL","").rstrip("/")
    store=os.environ.get("SHOPIFY_STORE","")
    if not store:raise ValueError("Set SHOPIFY_STORE on the worker, never in a theme file")
    if not base.startswith("https://"):raise ValueError("PUBLIC_BASE_URL must be the public HTTPS directory containing index.json")
    api=Shopify(store,admin_token(store))
    for race in index["races"]:
        if race["state"]!="ready":continue
        relative=f'races/{race["id"]}.json';data=json.loads((directory/relative).read_text())
        handle=f'race-{race["id"]}-{re.sub(r"[^a-z0-9]+","-",race["name"].lower()).strip("-")}'
        page=api.upsert(handle,f'{race["year"]} {race["name"]} — Results & Replay',race_body(data,"/pages/"+args.hub_handle),"race-replay","race_id",race["id"],base+"/"+relative,args.publish)
        if page.get("publishedAt"):race["page_url"]="/pages/"+handle
        else:race.pop("page_url",None)
        LOG.info("%s page prepared (%s)",handle,"published" if page.get("publishedAt") else "draft")
    api.upsert(args.hub_handle,"Race Centre",hub_body(index,"/pages/race-replay"),"race-centre","hub_id","bh-race-centre-v1",publish=args.publish,adopt_hub=True)
    atomic_json(directory/"index.json",index)

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",default="public");p.add_argument("--hub-handle",default="race-centre");p.add_argument("--publish",action="store_true")
    run(p.parse_args())
