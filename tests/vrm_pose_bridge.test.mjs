import test from 'node:test';
import assert from 'node:assert/strict';
import {validateBoneMap, validatePose, createPoseProjector, POSE_SCHEMA, MAPPING_SCHEMA} from '../tools/vrm_pose_bridge.mjs';

const mapping = {schema: MAPPING_SCHEMA, joints: {root: 'hips', skull: 'head', left_arm: 'leftUpperArm'}};
const frame = (tick, changes={}) => ({schema: POSE_SCHEMA, tick, source_revision:'rig-r01',
  coordinate_space:'vrm-normalized-local', root_meters:[0, 1, 0],
  joints:{root:[0,0,0,1], skull:[0,0,0,1], left_arm:[0,0,0,1]}, ...changes});
function fakeVrm(missing=null) {
  const calls=[];
  const bones = Object.fromEntries(Object.values(mapping.joints).filter(b=>b!==missing)
    .map(b=>[b,{rotation:{set(){}},quaternion:{set(...v){calls.push([b, v]);}}}]));
  return {calls, humanoid:{getNormalizedBoneNode:k=>bones[k] ?? null},
    scene:{position:{set(...v){calls.push(['root',v]);}}}};
}

test('offline deterministic ordered frames and exact duplicate are idempotent', ()=>{
  const vrm=fakeVrm(), p=createPoseProjector(vrm,mapping);
  assert.equal(p.apply(frame(3)), 'applied');
  const before=vrm.calls.length;
  assert.equal(p.apply(frame(3)), 'duplicate');
  assert.equal(vrm.calls.length,before);
  assert.equal(p.apply(frame(4,{root_meters:[1,1,0]})), 'applied');
  assert.equal(p.lastTick,4);
  assert.deepEqual(vrm.calls.at(-1),['root',[1,1,0]]);
  assert.throws(()=>p.apply(frame(6)), /discontinuity/);
  assert.throws(()=>p.apply(frame(3)), /discontinuity/);
  assert.equal(p.lastTick,4);
  p.reset();
  assert.equal(p.apply(frame(3)),'applied');
});

test('missing/extra mapped joints and bad humanoid maps fail closed', ()=>{
  const map=validateBoneMap(mapping);
  assert.equal(map.skull, 'head');
  assert.throws(()=>createPoseProjector(fakeVrm('head'),mapping),/missing/);
  assert.throws(()=>validatePose(frame(0,{joints:{root:[0,0,0,1]}}),map),/missing/);
  assert.throws(()=>validatePose(frame(0,{joints:{...frame(0).joints, extra:[0,0,0,1]}}),map),/unexpected field/);
  assert.throws(()=>validateBoneMap({...mapping,joints:{a:'head',b:'head'}}),/duplicate/);
  assert.throws(()=>validateBoneMap({...mapping,joints:{root:'allKnowingBrain'}}),/unknown/);
  assert.throws(()=>validateBoneMap({...mapping,joints:JSON.parse('{"__proto__":"head"}')}),/joint/);
});

test('invalid pose cannot partially mutate last valid VRM state', ()=>{
  const vrm=fakeVrm(), p=createPoseProjector(vrm,mapping);
  p.apply(frame(0));
  const size=vrm.calls.length;
  for (const bad of [
    frame(1,{joints:{...frame(1).joints, skull:[0,0,0,2]}}),
    frame(1,{joints:{...frame(1).joints, skull:[NaN,0,0,1]}}),
    frame(1,{root_meters:[Infinity,0,0]}),
    frame(1,{source_revision:'../hidden'}),
    frame(1,{coordinate_space:'world-space'}),
    frame(1,{tick:true}),
    {...frame(1), payload:'PRIVATE'},
    frame(0,{source_revision:'another-revision'}),
  ]) assert.throws(()=>p.apply(bad));
  assert.equal(vrm.calls.length,size);
  assert.equal(p.lastTick,0);
  assert.equal(p.apply(frame(1)), 'applied');
});

test('GLB guard rejects remote resources and malformed containers offline', async ()=>{
  const {inspectOfflineGlb} = await import('../tools/vrm_glb_guard.mjs');
  function glb(manifest) {
    const raw = Buffer.from(JSON.stringify(manifest));
    const padded = (raw.length + 3) & ~3;
    const data = new Uint8Array(20 + padded);
    const view = new DataView(data.buffer);
    view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true);
    view.setUint32(8, data.length, true); view.setUint32(12, padded, true);
    view.setUint32(16, 0x4e4f534a, true);
    data.set(raw,20);data.fill(32,20+raw.length);
    return data.buffer;
  }
  const empty={asset:{version:'2.0'},nodes:[],skins:[],buffers:[]};
  assert.equal(inspectOfflineGlb(glb(empty)), true);
  assert.throws(()=>inspectOfflineGlb(glb({...empty,images:[{uri:'https://elsewhere/x.png'}]})), /forbidden/);
  assert.throws(()=>inspectOfflineGlb(glb({...empty,extensions:{custom:{uri:'data:foo'}}})), /URI/);
  const broken=glb(empty);new DataView(broken).setUint32(8,0,true);
  assert.throws(()=>inspectOfflineGlb(broken), /GLB/);
  assert.throws(()=>inspectOfflineGlb(new ArrayBuffer(32 * 1024 * 1024 + 1)), /size/);
});
