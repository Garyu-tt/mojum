"""Compare the browser's toy point-motion preview against actual Python Room.advance.

This is fixture-only CPU maths, not MuJoCo, not learning, not replay or SQLite.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from mojum.world_room import Room, advance, initial_state

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = (
    "const fs=require('fs');"
    "const W=require('./tools/room_walk_preview.js');"
    "const vectors=JSON.parse(fs.readFileSync(0,'utf8'));"
    "const result=vectors.map(({room,start,moves})=>{"
    "const source={width:room.width,height:room.height,"
    "obstacles:room.obstacles.map(([x,y,w,h])=>({x,y,w,h})),"
    "start:{x:start[0],y:start[1]}};"
    "let state=W.begin(source),rows=[];"
    "for(const [dx,dy] of moves){state=W.step(source,state,dx,dy);"
    "rows.push(state.last)}return rows;});"
    "process.stdout.write(JSON.stringify(result));"
)


@unittest.skipUnless(shutil.which("node"), "Node 22 required for independent JS point-motion")
class PythonPointRoomParity(unittest.TestCase):
    def test_swept_collisions_and_wall_paths_identical(self):
        scenarios = [
            {
                "room": {"width": 4.5, "height": 4,
                         "obstacles": [[2.0,1,.125,2.0]]},
                "start": [1.25,2.5],
                "moves": [[.25,0],[.5,0],[0,-.25],[0,.25],
                          [0,-.5],[.5,0],[0,1]],
            },
            {
                "room": {"width": 4, "height": 4,
                         "obstacles": [[2.0,1,.001,2.0]]},
                "start": [1,2],
                "moves": [[2,0],[0,-1.1],[2,0],[0,1.5],
                          [0,.6],[-2,0]],
            },
            {
                "room": {"width": 4, "height": 4, "obstacles": []},
                "start": [1,1],
                "moves": [[2,1],[2,0],[-3,-2],[-.01,0],
                          [0,4], [.001,0], [0,-5]],
            },
            {
                "room": {"width": 3, "height": 3,
                         "obstacles": [[1,1,1,1]]},
                "start": [.5,.5],
                "moves": [[.5,.5],[2,2],[.5,0],[0,.5],[.5,0]],
            },
        ]
        result = subprocess.run(
            ["node", "-e", PROGRAM],
            input=json.dumps(scenarios), text=True, cwd=ROOT,
            capture_output=True, timeout=25, check=True,
        )
        js = json.loads(result.stdout)
        self.assertEqual(len(js), len(scenarios))
        for i, scenario in enumerate(scenarios):
            definition = scenario["room"]
            room = Room(
                definition["width"], definition["height"],
                tuple(tuple(rect) for rect in definition["obstacles"]),
            )
            state = initial_state(room, *scenario["start"])
            for j, (dx,dy) in enumerate(scenario["moves"]):
                next_state, frame = advance(
                    room, state, tick=state.tick + 1,
                    dx=dx, dy=dy, expected_revision=state.revision,
                )
                self.assertEqual(
                    js[i][j],
                    {
                        "tick":frame["tick"],
                        "position":frame["position"],
                        "blocked":frame["blocked"],
                        "provenance":frame["provenance"],
                        "physical_contact":frame["physical_contact"],
                    },
                    (i,j),
                )
                state = next_state


if __name__ == "__main__":
    unittest.main()
