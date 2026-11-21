"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const W = require("../tools/room_walk_preview.js");
const C = require("../tools/room_designer_core.js");
const example = () => C.validate(C.clone(C.DEFAULT));

test("preview starts at configured point and never claims persistent/physical contact",()=>{
  const room=example(), state=W.begin(room);
  assert.equal(state.schema,"mojum.world.point-room-walk-preview/1");
  assert.deepEqual([state.tick,state.x,state.y,state.blocked],[0,1.25,2.5,0]);
  assert.deepEqual(state.trail,[{x:1.25,y:2.5}]);
  const next=W.step(room,state,.25,0);
  assert.equal(next.x,1.5);
  assert.equal(next.last.provenance,"deterministic-point-room-fixture/v1");
  assert.equal(next.last.physical_contact,false);
  assert.equal(next.last.blocked,false);
  assert.equal(state.tick,0,"state is immutable");
});

test("closed obstacle edges and swept thin-wall tunneling are blocked",()=>{
  const room=example();
  let s=W.begin(room);
  s=W.step(room,s,.75,0);
  assert.equal(s.last.blocked,true);
  assert.deepEqual([s.x,s.y,s.blocked],[1.25,2.5,1]);
  assert.equal(W.segmentHitsRect(1.0,2,3.0,2,{x:2.0,y:1,w:.125,h:2.0}),true);
  assert.equal(W.segmentHitsRect(1.0,3.1,3.0,3.1,{x:2.0,y:1,w:.125,h:2.0}),false);
  const obstacle={width:4,height:4,start:{x:1,y:2},obstacles:[{x:2,y:1,w:.001,h:2}]};
  const crossed=W.step(obstacle,W.begin(obstacle),2,0);
  assert.equal(crossed.last.blocked,true);
  assert.deepEqual([crossed.x,crossed.y],[1,2]);
});

test("free movement, walls and negative outside room are deterministic",()=>{
  const room={width:4,height:4,start:{x:1,y:1},obstacles:[]};
  let s=W.begin(room);
  s=W.step(room,s,2,1);
  assert.deepEqual([s.x,s.y,s.tick],[3,2,1]);
  s=W.step(room,s,2,0);
  assert.equal(s.last.blocked,true);
  assert.deepEqual([s.x,s.y,s.tick,s.blocked],[3,2,2,1]);
  s=W.step(room,s,-3,-2);
  assert.deepEqual([s.x,s.y,s.tick],[0,0,3]);
  s=W.step(room,s,-.01,0);
  assert.deepEqual([s.x,s.y,s.tick,s.blocked],[0,0,4,2]);
});

test("invalid displacement and corrupted preview start fail closed",()=>{
  const room=example();
  const original=W.begin(room);
  for(const dx of [NaN,Infinity,-Infinity,1001]){
    assert.throws(()=>W.step(room,original,dx,0),/Некорректный/);
  }
  assert.throws(()=>W.begin({...room,start:{x:2.05,y:2}}),/стар.*комнаты/);
  assert.throws(()=>W.step(room,{...original,x:2.05,y:2},0,0),/Некорректный/);
});

test("corrupt counters and malformed trace history fail closed without mutation",()=>{
  const room=example(), first=W.begin(room);
  const good=W.step(room,first,.25,0);
  const before=JSON.stringify(good);
  const sparse=new Array(2);
  sparse[1]=good.trail[1];
  const forged=[
    {...good,blocked:-1},
    {...good,blocked:2},
    {...good,blocked:"0"},
    {...good,blocked:0.5},
    {...good,trail:[]},
    {...good,trail:null},
    {...good,trail:good.trail.concat(good.trail[1])},
    {...good,trail:sparse},
    {...good,trail:[good.trail[0],{x:3,y:3}]},
    {...good,trail:[{x:2.0,y:2.5},good.trail[1]]},
    {...good,trail:[{x:NaN,y:2.5},good.trail[1]]},
  ];
  for(const state of forged){
    assert.throws(()=>W.step(room,state,.25,0),/Некорректный шаг или исходное состояние/);
  }
  assert.equal(JSON.stringify(good),before,"rejecting a bad state never mutates a good one");
  assert.deepEqual(W.step(room,good,.25,0).last.position,[1.75,2.5]);
});

test("bounded 512-step trace does not allocate unbounded browser history",()=>{
  const room=example();let state=W.begin(room);
  for(let i=0;i<W.MAX_PREVIEW_STEPS;i++)state=W.step(room,state,0,0);
  assert.equal(state.tick,512);
  assert.equal(state.trail.length,513);
  assert.throws(()=>W.step(room,state,0,0),/512 шагов/);
});
