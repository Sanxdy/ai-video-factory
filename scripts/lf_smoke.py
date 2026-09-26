"""Phase 2 smoke test: create the countdown project and run up to storyboard.

Deliberately stops before assets/render so a bad script or a bad scene split
is caught in ~2 minutes instead of after a 35-minute render.
"""
import os
import sys

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.orchestrator.pipeline import create_project, get_db, build_stages
from apps.scripting.script_gen import generate_script
from apps.storyboard.storyboard_gen import generate_storyboard
from core.jobs import State
from core.settings import set_setting

TOPIC = os.environ.get("LF_TOPIC", "5 Places on Earth That Should Not Exist")
TARGET = int(os.environ.get("LF_TARGET", "330"))   # seconds (floor 300)
SCENE_DUR = 5       # seconds per scene (retention: visual change every 4-6s)

db = get_db()
pid = create_project(TOPIC)
db.update("projects", pid, aspect_ratio="16:9")
set_setting(f"project.{pid}.target_duration", str(TARGET))
set_setting(f"project.{pid}.max_scene_duration", str(SCENE_DUR))
print(f"PROJECT {pid}  {TOPIC!r}  16:9  target={TARGET}s  scene={SCENE_DUR}s",
      flush=True)

stages = build_stages(pid)
r = stages[State.RESEARCHING](pid, State.RESEARCHING)
print(f"stage RESEARCHING: success={r.success} {r.data or r.error}", flush=True)
if not r.success:
    raise SystemExit(1)

idea_id = db.get("projects", pid)["idea_id"]
# note: stage_script would do this too — call it directly so the script id is
# visible here instead of generating the script twice
sid = generate_script(db, idea_id, project_id=pid)
db.update("projects", pid, script_id=sid)
script = db.get("scripts", sid)
hook, body, cta = script["hook"], script["body"], script["cta"]
print(f"\nSCRIPT {sid}: duration={script['duration']}s")
print(f"words: hook={len(hook.split())} body={len(body.split())} "
      f"cta={len(cta.split())} total={len(f'{hook} {body} {cta}'.split())}")

ids = generate_storyboard(db, sid, project_id=pid)
scenes = db.all("scenes", "script_id=? ORDER BY scene_number", (sid,))
total = sum(float(s["duration"]) for s in scenes)
narr = sum(len((s["narration"] or "").split()) for s in scenes)
uniq = len({(s["visual_prompt"] or "")[:60] for s in scenes})
print(f"\nSTORYBOARD: {len(ids)} scenes, {total:.0f}s total, {narr} narration words")
print(f"unique visual prompts: {uniq}/{len(scenes)}")
print(f"stock queries filled: {sum(1 for s in scenes if s['stock_query'])}/{len(scenes)}")
print("\n--- HOOK ---\n" + hook)
print("\n--- FIRST ITEM HEADING/NARRATION (scene 6-8) ---")
for s in scenes[5:8]:
    print(f"[{s['scene_number']}] {s['narration']}")
    print(f"     visual: {s['visual_prompt'][:120]}")
    print(f"     query : {s['stock_query']}")
print("\n--- LAST SCENE ---\n" + scenes[-1]["narration"])
print("\nPID=" + str(pid) + " SID=" + str(sid))
